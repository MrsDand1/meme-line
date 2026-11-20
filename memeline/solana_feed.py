"""The Meme Line — direct Solana launch feed (replaces PumpPortal).

Subscribes to the pump.fun program via the Solana PUBLIC RPC WebSocket
(wss://api.mainnet-beta.solana.com, free, no key) with logsSubscribe,
filters the firehose client-side to token-create instructions only,
and extracts the launch from the create transaction's own logs: the
pump program emits a CreateEvent as a "Program data: <base64>" log
line, and that event carries name, symbol, uri, mint, bonding curve,
and creator — everything the pipeline needs, with ZERO RPC calls.

Fallback: when a create notification carries no CreateEvent line
(never observed in testing, but the format could change), the
signature is queued for one getTransaction call to extract the launch
the old way (see extract_launch). That path is throttled to one fetch
per 0.3 s and retried once, then the candidate is dropped and logged.

Emits launch events in the same shape pumpportal.py produced, so the
rest of the pipeline (capture, scoring, ledger, dashboard) is unchanged.

Diagnosis that drove this design (2026-11-20): a live smoke test of
the getTransaction-only build enriched only 5 of 28 create candidates
(82% drop rate). A follow-up diagnostic fetched one normal 88-char
signature at t+0, 1, 3, 5, 10, 30 s from the public RPC and got
200-null every time — so it is NOT replication lag; the public RPC
pool, as reached from this environment, will not serve fresh
transactions reliably. (Old transactions fetch fine, and a few fresh
ones do too — roughly 1 in 5.) The logs-event path sidesteps that
entire failure mode. On Render the service connects directly, so the
fallback may work better there, but the feed no longer depends on it.

Ground truth — verified live 2026-11-20 on the real Flyshake create
transaction (signature 4Fwg8cPkwxS5ghqfC2DbnRPP4k5EWs1HhwyraKN4phuvqWaBcX5
guGWoZMCPzhpgxAR9xcizTMp6DbMusX4aUAzq; see
tests/fixtures/solana-create-tx.json):
- Current launches use the CreateV2 instruction, which logs
  "Program log: Instruction: CreateV2". The legacy Create logs
  "Program log: Instruction: Create". Both are accepted; the traps
  "Program log: Create" (associated-token program) and
  "Instruction: CreateAccount" (system program) are NOT creates.
- A create transaction also carries a BuyV2 instruction (the creator's
  initial buy) in the SAME transaction, so the parser must pick the
  create instruction by discriminator, never just "a pump instruction".
  The same applies to the event lines: a create transaction also logs
  a TradeEvent ("Program data:" with a different discriminator), so
  the extractor matches the CreateEvent discriminator exactly.
- CreateEvent (Anchor event, discriminator = sha256("event:CreateEvent")
  [:8] = 1b72a94ddeeb6376): borsh name, symbol, uri, then mint,
  bonding_curve, user (creator) as 32-byte pubkeys. Verified against
  the Flyshake fixture: every field decodes to the known values.
- CreateV2 accounts, in order: mint, mint_authority, bonding_curve,
  associated_bonding_curve, global, user (creator), system_program,
  token_program, ... The creator is the fee payer (accountKeys[0]) —
  that cross-check works for Create and CreateV2 alike, so it is used
  instead of a fixed creator index (the two versions disagree on it).
- Instruction data is borsh: 8-byte discriminator, then name, symbol,
  uri as length-prefixed UTF-8 strings. CreateV2 discriminator
  d6904cec5f8b31b4; legacy Create 181e741cc9ba6f46.
- Create transactions are versioned (v0) with address lookup tables:
  instruction account indices resolve against static keys +
  loaded writable + loaded readonly, in that order.

Rate discipline: the public RPC allows ~40 requests per 10 s per
method. The primary (logs-event) path makes no RPC calls at all; the
getTransaction fallback is throttled to one fetch per 0.3 s (max ~3/s)
and retried once, then the candidate is dropped and logged — a missed
launch is skipped, never backfilled and never guessed. The raw event
stored in the database is a COMPACT dict (signature, slot, extracted
fields) — the full getTransaction result is ~18 KB per launch and
would blow the Turso free tier at 30-40 launches/min.

In this sandbox the egress proxy must be used for the WebSocket
handshake (read from the environment at runtime, never logged or
stored). On Render there is no proxy and the client connects directly.
"""
import asyncio
import base64
import json
import os
import struct
import time

import requests
import websockets

from . import config

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# Pump.fun program + create instruction discriminators (see module docstring).
CREATE_DISCRIMINATORS = {
    bytes([214, 144, 76, 236, 95, 139, 49, 180]),  # CreateV2: d6904cec5f8b31b4
    bytes([24, 30, 116, 28, 201, 186, 111, 70]),   # Create:   181e741cc9ba6f46
}
CREATE_LOG_LINES = {
    "Program log: Instruction: Create",
    "Program log: Instruction: CreateV2",
}
# Anchor CreateEvent discriminator: sha256("event:CreateEvent")[:8].
CREATE_EVENT_DISCRIMINATOR = bytes.fromhex("1b72a94ddeeb6376")
MIN_FETCH_GAP_SECONDS = 0.3  # at most ~3 getTransaction calls per second
_FETCH_RETRIES = 1           # one retry, then the candidate is dropped
_SEEN_MAX = 10_000           # cap the dedupe set (about 4 hours of creates)


def _proxy_from_env():
    """Proxy URL from the environment, if one is set. Never logged."""
    return os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or None


def _b58decode(data: str):
    """Decode a base58 string to bytes. Returns None on bad input."""
    if not isinstance(data, str):
        return None
    n = 0
    for ch in data:
        try:
            n = n * 58 + _B58_ALPHABET.index(ch)
        except ValueError:
            return None
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(data) - len(data.lstrip("1"))
    return b"\x00" * pad + raw


# ---------------------------------------------------------------------------
# Pure parsing — no network, no database
# ---------------------------------------------------------------------------

def is_create_notification(value):
    """True when a logsSubscribe notification value is a successful
    token-create transaction: err is null and the logs carry exactly a
    Create / CreateV2 instruction line from the pump program."""
    if not isinstance(value, dict):
        return False
    if value.get("err") is not None:
        return False
    logs = value.get("logs")
    if not isinstance(logs, list):
        return False
    return any(line in CREATE_LOG_LINES for line in logs if isinstance(line, str))


def decode_create_data(data: bytes):
    """Decode a pump.fun create instruction's data.

    Borsh: 8-byte discriminator, then name, symbol, uri as u32-length-
    prefixed UTF-8 strings. Returns (name, symbol, uri) or None when
    the discriminator is not a create or the data is truncated.
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
        return None
    if bytes(data[:8]) not in CREATE_DISCRIMINATORS:
        return None
    off = 8
    parts = []
    for _ in range(3):
        if off + 4 > len(data):
            return None
        ln = struct.unpack_from("<I", data, off)[0]
        off += 4
        if off + ln > len(data):
            return None
        try:
            parts.append(data[off:off + ln].decode("utf-8"))
        except UnicodeDecodeError:
            return None
        off += ln
    return parts[0], parts[1], parts[2]


def _b58encode(raw: bytes):
    """Encode bytes as a base58 string (inverse of _b58decode)."""
    if not isinstance(raw, (bytes, bytearray)):
        return None
    n = int.from_bytes(raw, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


def _read_borsh_string(data: bytes, off: int):
    """Read one u32-length-prefixed UTF-8 string. (string, new_off) or
    (None, None) when truncated, overlong, or not valid UTF-8."""
    if off + 4 > len(data):
        return None, None
    ln = struct.unpack_from("<I", data, off)[0]
    off += 4
    if ln > 1_000_000 or off + ln > len(data):
        return None, None
    try:
        return data[off:off + ln].decode("utf-8"), off + ln
    except UnicodeDecodeError:
        return None, None


def extract_launch_from_logs(log_messages, signature):
    """Extract a launch from a logsSubscribe notification's log lines.

    The pump program emits a CreateEvent as a "Program data: <base64>"
    log line: borsh discriminator, then name, symbol, uri, then mint,
    bonding_curve, user (creator) as 32-byte pubkeys. (The event may
    carry further trailing fields; they are ignored.) Returns the event
    dict in the PumpPortal shape, or None when no CreateEvent line is
    present or it does not decode. Fails closed: a wrong discriminator
    (e.g. the TradeEvent from the same transaction's BuyV2), a bad
    base64 line, or a truncated event all mean "not a launch here" —
    the caller falls back to getTransaction enrichment.

    The associated bonding-curve key is NOT in the event, so it is ""
    — exactly as the PumpPortal feed left it; capture resolves the
    curve account via getTokenAccountsByOwner at evaluation time.
    """
    if not isinstance(log_messages, list) or not isinstance(signature, str):
        return None
    for line in log_messages:
        if not isinstance(line, str) \
                or not line.startswith("Program data: "):
            continue
        try:
            raw = base64.b64decode(line[len("Program data: "):].strip())
        except Exception:  # noqa: BLE001 - a bad line is skipped, not fatal
            continue
        if len(raw) < 8 or bytes(raw[:8]) != CREATE_EVENT_DISCRIMINATOR:
            continue
        off = 8
        name, off = _read_borsh_string(raw, off)
        if name is None:
            continue
        symbol, off = _read_borsh_string(raw, off)
        if symbol is None:
            continue
        uri, off = _read_borsh_string(raw, off)
        if uri is None:
            continue
        if off + 96 > len(raw):
            continue
        mint = _b58encode(raw[off:off + 32])
        curve = _b58encode(raw[off + 32:off + 64])
        creator = _b58encode(raw[off + 64:off + 96])
        if not all(x and len(x) in (43, 44)
                   for x in (mint, curve, creator)):
            continue
        if not name or not symbol or not uri:
            continue
        return {
            "mint": mint,
            "name": name,
            "symbol": symbol,
            "uri": uri,
            "creator": creator,
            "bonding_curve_key": curve,
            "associated_curve_key": "",
            "pool": "pump",
            "v_tokens_in_curve": None,
            "v_sol_in_curve": None,
            "market_cap_sol": None,
            "raw": {
                "feed": "solana",
                "source": "logs_event",
                "signature": signature,
                "mint": mint,
                "creator": creator,
                "name": name,
                "symbol": symbol,
                "uri": uri,
                "bondingCurveKey": curve,
                "associatedBondingCurveKey": "",
            },
        }
    return None


def _full_key_list(tx):
    """Static account keys + address-table-loaded keys, in index order.

    Instruction account indices in a versioned transaction resolve
    against static keys, then loaded writable, then loaded readonly.
    """
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = list(msg.get("accountKeys") or [])
    loaded = ((tx.get("meta") or {}).get("loadedAddresses")) or {}
    keys += list(loaded.get("writable") or [])
    keys += list(loaded.get("readonly") or [])
    return keys


def _resolve_accounts(tx, instruction):
    """Instruction account indices -> base58 pubkeys. None on bad shape."""
    keys = _full_key_list(tx)
    out = []
    for idx in instruction.get("accounts") or []:
        if not isinstance(idx, int) or idx < 0 or idx >= len(keys):
            return None
        out.append(keys[idx])
    return out


def extract_launch(tx, signature):
    """Extract a launch event from a getTransaction result (json encoding).

    Returns the event dict in the PumpPortal shape, or None when the
    transaction is not a verifiable pump.fun create. Fails closed: a
    wrong discriminator, a creator that is not the fee payer, or a mint
    missing from the token balances all mean "not a launch".
    """
    if not isinstance(tx, dict):
        return None
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None
    msg = (tx.get("transaction") or {}).get("message") or {}
    account_keys = msg.get("accountKeys") or []
    if not account_keys:
        return None
    fee_payer = account_keys[0]
    instructions = msg.get("instructions") or []
    create_ix = None
    for ix in instructions:
        if not isinstance(ix, dict):
            continue
        prog_idx = ix.get("programIdIndex")
        if not isinstance(prog_idx, int):
            continue
        if prog_idx < 0 or prog_idx >= len(account_keys):
            continue
        if account_keys[prog_idx] != config.PUMP_PROGRAM_ID:
            continue
        raw_data = _b58decode(ix.get("data") or "")
        if raw_data is None or len(raw_data) < 8:
            continue
        if bytes(raw_data[:8]) in CREATE_DISCRIMINATORS:
            create_ix = (ix, raw_data)
            break
    if create_ix is None:
        return None
    ix, raw_data = create_ix
    decoded = decode_create_data(raw_data)
    if decoded is None:
        return None
    name, symbol, uri = decoded
    accounts = _resolve_accounts(tx, ix)
    # Create / CreateV2 layout: mint, mint_authority, bonding_curve,
    # associated_bonding_curve, global, user(creator), ...
    if accounts is None or len(accounts) < 6:
        return None
    mint, bonding_curve_key, associated_curve_key = (
        accounts[0], accounts[2], accounts[3])
    creator = fee_payer
    # Cross-check: the mint must appear in the transaction's token
    # balances, or the account layout is not what we think it is.
    balance_mints = {
        b.get("mint") for b in (meta.get("postTokenBalances") or [])
        if isinstance(b, dict)
    }
    if mint not in balance_mints:
        return None
    return {
        "mint": mint,
        "name": name,
        "symbol": symbol,
        "uri": uri,
        "creator": creator,
        "bonding_curve_key": bonding_curve_key,
        "associated_curve_key": associated_curve_key,
        "pool": "pump",
        "v_tokens_in_curve": None,
        "v_sol_in_curve": None,
        "market_cap_sol": None,
        "raw": {
            "feed": "solana",
            "signature": signature,
            "slot": tx.get("slot"),
            "blockTime": tx.get("blockTime"),
            "mint": mint,
            "creator": creator,
            "name": name,
            "symbol": symbol,
            "uri": uri,
            "bondingCurveKey": bonding_curve_key,
            "associatedBondingCurveKey": associated_curve_key,
        },
    }


# ---------------------------------------------------------------------------
# Enrichment fallback — one getTransaction per create candidate, throttled.
# Only used when the logs carry no CreateEvent (see extract_launch_from_logs).
# ---------------------------------------------------------------------------

def fetch_transaction(signature):
    """Fetch one transaction from the public RPC (json encoding).

    Returns the result dict, or None when the call fails, the RPC
    rate-limits us, or the transaction is not (yet) available.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
               "params": [signature,
                          {"encoding": "json",
                           "maxSupportedTransactionVersion": 0,
                           "commitment": "confirmed"}]}
    try:
        r = requests.post(config.SOLANA_PUBLIC_RPC_URL, json=payload,
                          timeout=config.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    return result if isinstance(result, dict) else None


async def _consume_signatures(queue, on_launch, stop_event, seen):
    """Enrich queued create signatures into launch events, one at a time.

    Throttled to one fetch per MIN_FETCH_GAP_SECONDS; each candidate is
    retried once, then dropped with a log line. A dropped candidate is
    a missed launch — skipped, never backfilled, never guessed.
    """
    last_fetch = 0.0
    while stop_event is None or not stop_event.is_set():
        try:
            sig = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        now = time.monotonic()
        gap = MIN_FETCH_GAP_SECONDS - (now - last_fetch)
        if gap > 0:
            try:
                await asyncio.wait_for(_wait_stop(stop_event), timeout=gap)
            except asyncio.TimeoutError:
                pass
            if stop_event is not None and stop_event.is_set():
                return
        last_fetch = time.monotonic()
        tx = None
        for attempt in range(_FETCH_RETRIES + 1):
            tx = await asyncio.to_thread(fetch_transaction, sig)
            if tx is not None:
                break
            if attempt < _FETCH_RETRIES:
                try:
                    await asyncio.wait_for(_wait_stop(stop_event), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                if stop_event is not None and stop_event.is_set():
                    return  # service is stopping; do not retry into shutdown
        if tx is None:
            print(f"[solana-feed] dropped candidate {sig[:12]}...: "
                  "getTransaction failed")
            continue
        ev = extract_launch(tx, sig)
        if ev is None:
            print(f"[solana-feed] dropped candidate {sig[:12]}...: "
                  "not a verifiable create")
            continue
        on_launch(ev)


# ---------------------------------------------------------------------------
# Feed loop — same callbacks and gap discipline as pumpportal.run_feed
# ---------------------------------------------------------------------------

async def run_feed(on_launch, on_migration, on_gap, on_reconnect=None,
                   stop_event=None):
    """Run the Solana direct feed forever (until stop_event is set).

    on_launch(event_dict) is a sync callback. on_gap(started_at, reason)
    is called on every disconnect; on_reconnect is called after each
    (re)connection, so the caller can close the gap. Reconnects with
    backoff from config.WS_RECONNECT_BACKOFF. on_migration is accepted
    for interface parity but never called: graduation is detected at
    evaluation time via DexScreener, not from this feed.
    """
    backoff_idx = 0
    while stop_event is None or not stop_event.is_set():
        queue = asyncio.Queue(maxsize=1000)
        seen = set()
        consumer = asyncio.create_task(
            _consume_signatures(queue, on_launch, stop_event, seen))
        try:
            proxy = _proxy_from_env()
            kwargs = {"max_size": 10 * 1024 * 1024, "open_timeout": 30}
            if proxy:
                kwargs["proxy"] = proxy
            async with websockets.connect(config.SOLANA_WS_URL, **kwargs) as ws:
                backoff_idx = 0
                if on_reconnect is not None:
                    on_reconnect()
                print("[solana-feed] connected, subscribed to pump.fun logs")
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                    "params": [{"mentions": [config.PUMP_PROGRAM_ID]},
                               {"commitment": "confirmed"}],
                }))
                while stop_event is None or not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    except asyncio.TimeoutError:
                        continue  # idle keep-alive; connection still open
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(msg, dict) and "error" in msg \
                            and "method" not in msg:
                        err = msg.get("error") or {}
                        raise RuntimeError(
                            "logsSubscribe rejected: "
                            f"{err.get('message') if isinstance(err, dict) else err}")
                    if not isinstance(msg, dict) \
                            or msg.get("method") != "logsNotification":
                        continue
                    value = ((msg.get("params") or {}).get("result") or {}
                             ).get("value") or {}
                    if not is_create_notification(value):
                        continue
                    sig = value.get("signature")
                    if not isinstance(sig, str) or sig in seen:
                        continue
                    seen.add(sig)
                    if len(seen) > _SEEN_MAX:
                        seen.clear()
                        seen.add(sig)
                    # Primary path: the create transaction's own logs
                    # carry the CreateEvent — extract the launch
                    # directly, no RPC call. (Verified live 2026-11-20:
                    # the public RPC will not serve most fresh
                    # transactions, so the feed must not depend on it.)
                    launch = extract_launch_from_logs(
                        value.get("logs"), sig)
                    if launch is not None:
                        slot = ((msg.get("params") or {}).get("result")
                                or {}).get("context", {}).get("slot")
                        launch["raw"]["slot"] = slot
                        on_launch(launch)
                        continue
                    # Fallback: no CreateEvent in the logs — enrich via
                    # getTransaction (throttled, one retry, then drop).
                    try:
                        queue.put_nowait(sig)
                    except asyncio.QueueFull:
                        print(f"[solana-feed] dropped candidate "
                              f"{sig[:12]}...: enrichment queue full")
        except Exception as e:  # noqa: BLE001 - reconnect on any failure
            print(f"[solana-feed] disconnected: {type(e).__name__}")
            if on_gap is not None:
                on_gap(time.time(), f"ws_disconnect: {type(e).__name__}")
            wait = config.WS_RECONNECT_BACKOFF[
                min(backoff_idx, len(config.WS_RECONNECT_BACKOFF) - 1)
            ]
            backoff_idx += 1
            try:
                await asyncio.wait_for(_wait_stop(stop_event), timeout=wait)
            except asyncio.TimeoutError:
                pass
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass


async def _wait_stop(stop_event):
    if stop_event is None:
        await asyncio.sleep(3600)
        return
    await stop_event.wait()
