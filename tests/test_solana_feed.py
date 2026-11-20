"""Solana direct-feed tests.

Parsing tests use the REAL Flyshake create transaction, fetched live
from the public RPC 2026-11-20 (see tests/fixtures/solana-create-tx.json)
and the matching PumpPortal event in pumpportal-new-token.json. The
feed-loop test uses a fake WebSocket — no network.

The feed has two extraction paths: the primary path reads the
CreateEvent from the notification's own log lines (no RPC call), and
the fallback enriches via getTransaction when no CreateEvent is in
the logs (see tests below for both).
"""
import asyncio
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline import capture, db, solana_feed  # noqa: E402
from memeline.scoring import prefilter  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
FLYSHAKE_SIG = ("4Fwg8cPkwxS5ghqfC2DbnRPP4k5EWs1HhwyraKN4phuvqWaBcX5guGWoZM"
                "CPzhpgxAR9xcizTMp6DbMusX4aUAzq")
FLYSHAKE_MINT = "9112ktmpXnFYbULPqkg1J9PhiHHEVDE1uBJ6ayWapump"
FLYSHAKE_CREATOR = "DkFDkr95h7A19HMqiF5g8A4YXkjxp2kmeCibJ6MLbpdr"
FLYSHAKE_CURVE = "46GCWN3GxVejz1nFLPT6xYm1oTo9gUogG4aM8qX46Ro6"
FLYSHAKE_ASSOC_CURVE = "AzqohA7Tmoj41BU5ZLLrE74FYXv9hSKa9z9yRdUPdoGt"


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _tx():
    return _load("solana-create-tx.json")


def _create_notification():
    tx = _tx()
    return {"signature": FLYSHAKE_SIG, "err": None,
            "logs": tx["meta"]["logMessages"]}


def _create_notification_without_event():
    """Same create notification, but with the "Program data:" lines
    stripped — forces the getTransaction fallback path."""
    note = _create_notification()
    note["logs"] = [l for l in note["logs"]
                    if not l.startswith("Program data: ")]
    return note


# ---------------------------------------------------------------------------
# Log filtering — create vs trade vs traps
# ---------------------------------------------------------------------------

def test_real_create_logs_are_a_candidate():
    assert solana_feed.is_create_notification(_create_notification()) is True


def test_buy_and_sell_logs_are_not_candidates():
    base = {"signature": "S", "err": None}
    assert solana_feed.is_create_notification(
        {**base, "logs": ["Program log: Instruction: BuyV2"]}) is False
    assert solana_feed.is_create_notification(
        {**base, "logs": ["Program log: Instruction: Sell"]}) is False
    assert solana_feed.is_create_notification(
        {**base, "logs": ["Program log: Instruction: Migrate"]}) is False


def test_create_traps_are_not_candidates():
    """The associated-token program logs bare "Create", and the system
    program logs "CreateAccount" — neither is a pump.fun create."""
    base = {"signature": "S", "err": None}
    assert solana_feed.is_create_notification(
        {**base, "logs": ["Program log: Create"]}) is False
    assert solana_feed.is_create_notification(
        {**base, "logs": ["Program log: Instruction: CreateAccount"]}) is False
    assert solana_feed.is_create_notification(
        {**base, "logs": ["Program log: Instruction: CreateIdempotent"]}) is False


def test_failed_tx_and_bad_shapes_are_not_candidates():
    assert solana_feed.is_create_notification(
        {"signature": "S", "err": {"InstructionError": [0, "Custom"]},
         "logs": ["Program log: Instruction: CreateV2"]}) is False
    assert solana_feed.is_create_notification(
        {"signature": "S", "err": None}) is False
    assert solana_feed.is_create_notification("not a dict") is False
    assert solana_feed.is_create_notification(None) is False


# ---------------------------------------------------------------------------
# Instruction-data decoding
# ---------------------------------------------------------------------------

def test_decode_real_create_data():
    tx = _tx()
    ix = tx["transaction"]["message"]["instructions"][2]
    raw = solana_feed._b58decode(ix["data"])
    assert raw[:8].hex() == "d6904cec5f8b31b4"  # CreateV2 discriminator
    name, symbol, uri = solana_feed.decode_create_data(raw)
    assert name == "Flyshake"
    assert symbol == "FLYSHAKE"
    assert uri == ("https://ipfs.io/ipfs/bafkreicx4wtkvsuk6qbjhrqyudkxgkoihfh5zni"
                    "5b3p7ej3ufxu5rugyzu")


def test_decode_rejects_non_create_and_truncated():
    tx = _tx()
    buy = tx["transaction"]["message"]["instructions"][4]
    raw = solana_feed._b58decode(buy["data"])
    assert raw[:8].hex() == "b817ee6167c5d33d"  # BuyV2, not a create
    assert solana_feed.decode_create_data(raw) is None
    assert solana_feed.decode_create_data(b"\x00" * 4) is None
    assert solana_feed.decode_create_data(b"") is None
    assert solana_feed.decode_create_data("not bytes") is None


def test_b58_decode_roundtrip():
    raw = solana_feed._b58decode(FLYSHAKE_MINT)
    assert raw is not None and len(raw) == 32
    assert solana_feed._b58decode("not base58!@#") is None
    assert solana_feed._b58decode(123) is None


def test_b58_encode_roundtrip():
    assert solana_feed._b58encode(
        solana_feed._b58decode(FLYSHAKE_MINT)) == FLYSHAKE_MINT
    assert solana_feed._b58encode(b"\x00" * 32) == "1" * 32
    assert solana_feed._b58encode("not bytes") is None


# ---------------------------------------------------------------------------
# Transaction extraction — the real Flyshake create
# ---------------------------------------------------------------------------

def test_extract_real_create_matches_pumpportal_event():
    ev = solana_feed.extract_launch(_tx(), FLYSHAKE_SIG)
    assert ev is not None
    assert ev["mint"] == FLYSHAKE_MINT
    assert ev["name"] == "Flyshake"
    assert ev["symbol"] == "FLYSHAKE"
    assert ev["uri"] == ("https://ipfs.io/ipfs/bafkreicx4wtkvsuk6qbjhrqyudkxgkoihf"
                         "h5zni5b3p7ej3ufxu5rugyzu")
    assert ev["creator"] == FLYSHAKE_CREATOR
    assert ev["bonding_curve_key"] == FLYSHAKE_CURVE
    # The Solana feed supplies the associated curve key directly; the
    # PumpPortal event never carried one.
    assert ev["associated_curve_key"] == FLYSHAKE_ASSOC_CURVE
    assert ev["pool"] == "pump"
    assert ev["raw"]["feed"] == "solana"
    assert ev["raw"]["signature"] == FLYSHAKE_SIG
    # The compact raw must stay small: the full tx is ~18 KB.
    assert len(json.dumps(ev["raw"])) < 1500


def test_extract_rejects_buy_only_transaction():
    tx = _tx()
    # Replace the create instruction's data with the BuyV2 data: the
    # transaction is no longer a create.
    tx = copy.deepcopy(tx)
    ix = tx["transaction"]["message"]["instructions"]
    ix[2]["data"] = ix[4]["data"]
    assert solana_feed.extract_launch(tx, FLYSHAKE_SIG) is None


def test_extract_rejects_shifted_account_layout():
    """If the mint is not where we expect it, the mint-in-balances
    cross-check fails and the candidate is dropped, never guessed."""
    tx = copy.deepcopy(_tx())
    ix = tx["transaction"]["message"]["instructions"][2]
    ix["accounts"][0], ix["accounts"][1] = ix["accounts"][1], ix["accounts"][0]
    assert solana_feed.extract_launch(tx, FLYSHAKE_SIG) is None


def test_extract_rejects_mint_missing_from_balances():
    tx = copy.deepcopy(_tx())
    tx["meta"]["postTokenBalances"] = [
        b for b in tx["meta"]["postTokenBalances"]
        if b["mint"] != FLYSHAKE_MINT]
    assert solana_feed.extract_launch(tx, FLYSHAKE_SIG) is None


def test_extract_rejects_failed_or_empty_transactions():
    tx = copy.deepcopy(_tx())
    tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    assert solana_feed.extract_launch(tx, FLYSHAKE_SIG) is None
    assert solana_feed.extract_launch({}, FLYSHAKE_SIG) is None
    assert solana_feed.extract_launch(None, FLYSHAKE_SIG) is None


# ---------------------------------------------------------------------------
# Logs-event extraction — the primary path (no RPC call)
# ---------------------------------------------------------------------------

def test_extract_launch_from_logs_real_flyshake_event():
    """The CreateEvent in the real Flyshake logs decodes to exactly the
    launch the getTransaction path produces (minus the associated
    curve key, which the event does not carry)."""
    ev = solana_feed.extract_launch_from_logs(
        _tx()["meta"]["logMessages"], FLYSHAKE_SIG)
    assert ev is not None
    assert ev["mint"] == FLYSHAKE_MINT
    assert ev["name"] == "Flyshake"
    assert ev["symbol"] == "FLYSHAKE"
    assert ev["uri"] == ("https://ipfs.io/ipfs/bafkreicx4wtkvsuk6qbjhrqyudkxgkoihf"
                         "h5zni5b3p7ej3ufxu5rugyzu")
    assert ev["creator"] == FLYSHAKE_CREATOR
    assert ev["bonding_curve_key"] == FLYSHAKE_CURVE
    # The event carries no associated curve key — same as PumpPortal.
    assert ev["associated_curve_key"] == ""
    assert ev["pool"] == "pump"
    assert ev["raw"]["feed"] == "solana"
    assert ev["raw"]["source"] == "logs_event"
    assert ev["raw"]["signature"] == FLYSHAKE_SIG
    # The compact raw must stay small: the full tx is ~18 KB.
    assert len(json.dumps(ev["raw"])) < 1500


def test_extract_launch_from_logs_skips_trade_event():
    """The same transaction also logs a TradeEvent (its BuyV2) as a
    "Program data:" line — a different discriminator, so it must not
    be mistaken for a create."""
    tx = _tx()
    pd_lines = [l for l in tx["meta"]["logMessages"]
                if l.startswith("Program data: ")]
    assert len(pd_lines) == 2  # CreateEvent, then TradeEvent
    assert solana_feed.extract_launch_from_logs(
        [pd_lines[1]], FLYSHAKE_SIG) is None


def test_extract_launch_from_logs_rejects_bad_lines():
    assert solana_feed.extract_launch_from_logs([], FLYSHAKE_SIG) is None
    assert solana_feed.extract_launch_from_logs(
        ["Program data: !!!not-base64!!!"], FLYSHAKE_SIG) is None
    # Valid base64, wrong discriminator (all zeroes).
    import base64
    assert solana_feed.extract_launch_from_logs(
        ["Program data: " + base64.b64encode(b"\x00" * 100).decode()],
        FLYSHAKE_SIG) is None
    # Truncated CreateEvent: discriminator right, fields cut off.
    raw = base64.b64decode(
        [l for l in _tx()["meta"]["logMessages"]
         if l.startswith("Program data: ")][0][len("Program data: "):])
    assert solana_feed.extract_launch_from_logs(
        ["Program data: " + base64.b64encode(raw[:20]).decode()],
        FLYSHAKE_SIG) is None
    assert solana_feed.extract_launch_from_logs(None, FLYSHAKE_SIG) is None
    assert solana_feed.extract_launch_from_logs("not a list", FLYSHAKE_SIG) is None


def test_logs_event_passes_prefilter_and_stores():
    """The logs-event launch must flow through the unchanged pipeline."""
    import tempfile
    ev = solana_feed.extract_launch_from_logs(
        _tx()["meta"]["logMessages"], FLYSHAKE_SIG)
    assert prefilter(ev)[0] is True
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = db.connect(database_path=path)
    db.init_db(conn)
    capture.store_launch(conn, ev)
    row = db.fetchone_dict(conn, "SELECT * FROM raw_launches WHERE mint=?",
                           (FLYSHAKE_MINT,))
    assert row is not None
    assert row["creator"] == FLYSHAKE_CREATOR
    assert row["bonding_curve_key"] == FLYSHAKE_CURVE
    assert row["associated_curve_key"] == ""


def test_solana_event_passes_prefilter_and_stores():
    """The Solana event must flow through the unchanged pipeline."""
    import tempfile
    ev = solana_feed.extract_launch(_tx(), FLYSHAKE_SIG)
    assert prefilter(ev)[0] is True
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = db.connect(database_path=path)
    db.init_db(conn)
    capture.store_launch(conn, ev)
    row = db.fetchone_dict(conn, "SELECT * FROM raw_launches WHERE mint=?",
                           (FLYSHAKE_MINT,))
    assert row is not None
    assert row["creator"] == FLYSHAKE_CREATOR
    assert row["bonding_curve_key"] == FLYSHAKE_CURVE
    assert row["associated_curve_key"] == FLYSHAKE_ASSOC_CURVE


# ---------------------------------------------------------------------------
# Config switch
# ---------------------------------------------------------------------------

def test_select_feed_runner_defaults_to_solana(monkeypatch):
    from memeline import pumpportal
    monkeypatch.setattr("memeline.config.FEED_SOURCE", "solana")
    assert capture.select_feed_runner() is solana_feed.run_feed
    monkeypatch.setattr("memeline.config.FEED_SOURCE", "pumpportal")
    assert capture.select_feed_runner() is pumpportal.run_feed
    monkeypatch.setattr("memeline.config.FEED_SOURCE", "anything-else")
    assert capture.select_feed_runner() is solana_feed.run_feed


# ---------------------------------------------------------------------------
# Feed loop with a fake WebSocket — no network
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self, messages, delivered):
        self._messages = list(messages)
        self.sent = []
        self._delivered = delivered

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.wait_for(self._delivered.wait(), timeout=5)
        raise ConnectionResetError("fake disconnect")


def test_run_feed_fake_ws(monkeypatch):
    tx = _tx()
    note = {"jsonrpc": "2.0", "method": "logsNotification",
            "params": {"result": {"context": {"slot": tx["slot"]},
                                  "value": _create_notification()},
                       "subscription": 7}}
    messages = [json.dumps({"jsonrpc": "2.0", "result": 7, "id": 1}),
                json.dumps(note)]
    delivered = asyncio.Event()
    launched = []
    gaps = []
    reconnects = []
    fake_ws = _FakeWS(messages, delivered)
    stop_event = asyncio.Event()

    def on_launch(ev):
        launched.append(ev)
        delivered.set()
        stop_event.set()

    def on_gap(started_at, reason):
        gaps.append(reason)

    def on_reconnect():
        reconnects.append(True)

    monkeypatch.setattr(solana_feed, "fetch_transaction",
                        lambda sig: copy.deepcopy(tx))
    monkeypatch.setattr("memeline.config.WS_RECONNECT_BACKOFF", [0.01])
    monkeypatch.setattr(solana_feed.websockets, "connect",
                        lambda url, **kw: fake_ws)

    async def _run():
        await solana_feed.run_feed(on_launch, lambda e: None, on_gap,
                                   on_reconnect, stop_event)

    asyncio.run(asyncio.wait_for(_run(), timeout=15))
    assert len(launched) == 1
    assert launched[0]["mint"] == FLYSHAKE_MINT
    assert launched[0]["creator"] == FLYSHAKE_CREATOR
    assert len(reconnects) == 1
    # The launch is delivered synchronously in the WS loop, so the
    # stop_event set by on_launch ends the loop cleanly before the
    # fake disconnect fires — a clean shutdown logs no gap.
    assert gaps == []
    # The subscription request went out first, for the pump program.
    sub = json.loads(fake_ws.sent[0])
    assert sub["method"] == "logsSubscribe"
    assert sub["params"][0] == {"mentions": [PUMP_PROGRAM]}
    assert sub["params"][1] == {"commitment": "confirmed"}


def test_run_feed_uses_logs_event_without_rpc(monkeypatch):
    """Primary path: the launch comes from the notification's own logs,
    so getTransaction must never be called. (A fetch that raises here
    would fail the test — the RPC is not consulted at all.)"""
    note = {"jsonrpc": "2.0", "method": "logsNotification",
            "params": {"result": {"context": {"slot": 471584041},
                                  "value": _create_notification()},
                       "subscription": 7}}
    messages = [json.dumps({"jsonrpc": "2.0", "result": 7, "id": 1}),
                json.dumps(note)]
    delivered = asyncio.Event()
    launched = []
    stop_event = asyncio.Event()
    fake_ws = _FakeWS(messages, delivered)

    def on_launch(ev):
        launched.append(ev)
        delivered.set()
        stop_event.set()

    def _boom(sig):
        raise AssertionError("getTransaction must not be called on the "
                             "logs-event path")

    monkeypatch.setattr(solana_feed, "fetch_transaction", _boom)
    monkeypatch.setattr("memeline.config.WS_RECONNECT_BACKOFF", [0.01])
    monkeypatch.setattr(solana_feed.websockets, "connect",
                        lambda url, **kw: fake_ws)

    async def _run():
        await solana_feed.run_feed(on_launch, lambda e: None,
                                   lambda *a: None, None, stop_event)

    asyncio.run(asyncio.wait_for(_run(), timeout=15))
    assert len(launched) == 1
    assert launched[0]["mint"] == FLYSHAKE_MINT
    assert launched[0]["raw"]["source"] == "logs_event"


def test_run_feed_falls_back_to_gettransaction_without_event(monkeypatch):
    """A create notification with no CreateEvent line in its logs still
    emits — via the getTransaction fallback — instead of being lost."""
    tx = _tx()
    note = {"jsonrpc": "2.0", "method": "logsNotification",
            "params": {"result": {"context": {"slot": tx["slot"]},
                                  "value": _create_notification_without_event()},
                       "subscription": 7}}
    messages = [json.dumps({"jsonrpc": "2.0", "result": 7, "id": 1}),
                json.dumps(note)]
    delivered = asyncio.Event()
    launched = []
    stop_event = asyncio.Event()
    fake_ws = _FakeWS(messages, delivered)

    def on_launch(ev):
        launched.append(ev)
        delivered.set()
        stop_event.set()

    monkeypatch.setattr(solana_feed, "fetch_transaction",
                        lambda sig: copy.deepcopy(tx))
    monkeypatch.setattr("memeline.config.WS_RECONNECT_BACKOFF", [0.01])
    monkeypatch.setattr(solana_feed.websockets, "connect",
                        lambda url, **kw: fake_ws)

    async def _run():
        await solana_feed.run_feed(on_launch, lambda e: None,
                                   lambda *a: None, None, stop_event)

    asyncio.run(asyncio.wait_for(_run(), timeout=15))
    assert len(launched) == 1
    assert launched[0]["mint"] == FLYSHAKE_MINT
    # This one came from the transaction, so the associated curve key
    # is present — proving the fallback path ran, not the event path.
    assert launched[0]["associated_curve_key"] == FLYSHAKE_ASSOC_CURVE


def test_run_feed_drops_unenrichable_candidate(monkeypatch, capsys):
    """A create notification whose transaction cannot be fetched is
    dropped with a log line — the feed keeps running. (Uses a
    notification with no CreateEvent, so the getTransaction fallback
    is what runs and fails.)"""
    note = {"jsonrpc": "2.0", "method": "logsNotification",
            "params": {"result": {"context": {"slot": 1},
                                  "value": _create_notification_without_event()},
                       "subscription": 7}}
    messages = [json.dumps({"jsonrpc": "2.0", "result": 7, "id": 1}),
                json.dumps(note)]
    delivered = asyncio.Event()
    launched = []
    gaps = []
    stop_event = asyncio.Event()
    fake_ws = _FakeWS(messages, delivered)

    def on_launch(ev):
        launched.append(ev)

    # The consumer drops the candidate, then idles; stop the feed from
    # a timer so the test ends. The timer fires at 1.5 s — after the
    # consumer's 1 s retry wait — so the drop is recorded first.
    async def _stopper():
        await asyncio.sleep(1.5)
        delivered.set()  # lets the fake WS raise and end the loop
        stop_event.set()

    monkeypatch.setattr(solana_feed, "fetch_transaction", lambda sig: None)
    monkeypatch.setattr("memeline.config.WS_RECONNECT_BACKOFF", [0.01])
    monkeypatch.setattr(solana_feed.websockets, "connect",
                        lambda url, **kw: fake_ws)

    async def _run():
        await asyncio.gather(
            solana_feed.run_feed(on_launch, lambda e: None,
                                 lambda *a: gaps.append(a), None, stop_event),
            _stopper())

    asyncio.run(asyncio.wait_for(_run(), timeout=15))
    assert launched == []
    assert "dropped candidate" in capsys.readouterr().out
    # The fake disconnect after the stopper still logs a gap.
    assert len(gaps) == 1
    assert gaps[0][1].startswith("ws_disconnect:")
