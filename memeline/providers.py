"""The Meme Line — provider clients (design section 2).

All free, all public. No key except Helius (read-only RPC, user holds it).
A 429 is a pause-and-retry, never a green light. A failed check is
reported as "unknown", never as a pass — the callers decide that.

Response shapes were verified live on 2026-11-19/20 against real tokens
(see tests/fixtures). Field access is defensive throughout: a missing
field is unknown, not zero.
"""
import base64
import time

import requests

from . import config

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ProviderError(Exception):
    pass


class RateLimited(ProviderError):
    pass


def _b58encode(raw: bytes) -> str:
    """Encode bytes as a base58 string (Solana address format)."""
    n = int.from_bytes(raw, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = 0
    for b in raw:
        if b == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def _get(url, params=None, timeout=None):
    try:
        r = requests.get(url, params=params,
                         timeout=timeout or config.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise ProviderError(f"GET {url} failed: {e}") from e
    if r.status_code == 429:
        raise RateLimited(f"GET {url} returned 429")
    if r.status_code >= 400:
        raise ProviderError(f"GET {url} returned {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ProviderError(f"GET {url} returned non-JSON") from e


def _post_json(url, payload, timeout=None):
    try:
        r = requests.post(url, json=payload,
                          timeout=timeout or config.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise ProviderError(f"POST {url} failed: {e}") from e
    if r.status_code == 429:
        raise RateLimited(f"POST {url} returned 429")
    if r.status_code >= 400:
        raise ProviderError(f"POST {url} returned {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ProviderError(f"POST {url} returned non-JSON") from e


# ---------------------------------------------------------------------------
# DexScreener — pair price, liquidity, volume, socials (free, no key)
# ---------------------------------------------------------------------------

def dexscreener_pairs(mint):
    """All DexScreener pairs for a mint. Returns a list (possibly empty)."""
    data = _get(f"{config.DEXSCREENER_BASE}/latest/dex/tokens/{mint}")
    return data.get("pairs") or []


def dexscreener_best_pair(mint):
    """Best pair by liquidity. Returns a dict or None when no pair exists.

    Some pairs (e.g. Meteora DBC) carry no `liquidity` key at all — that
    is unknown liquidity, not zero.
    """
    pairs = dexscreener_pairs(mint)
    if not pairs:
        return None
    sol_pairs = [p for p in pairs if p.get("chainId") == "solana"] or pairs

    def liq(p):
        v = (p.get("liquidity") or {}).get("usd")
        return float(v) if isinstance(v, (int, float)) else 0.0

    best = max(sol_pairs, key=liq)
    info = best.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []
    price = best.get("priceUsd")
    return {
        "price_usd": float(price) if price is not None else None,
        "liquidity_usd": (best.get("liquidity") or {}).get("usd"),
        "dex_id": best.get("dexId"),
        "pair_address": best.get("pairAddress"),
        "fdv": best.get("fdv"),
        "has_socials": bool(socials or websites),
        "socials": [s.get("url") for s in socials if s.get("url")],
        "websites": [w.get("url") for w in websites if w.get("url")],
    }


# ---------------------------------------------------------------------------
# RugCheck — risk score, flags, holders, creator (free, no key)
# ---------------------------------------------------------------------------
_last_rugcheck_call = 0.0


def rugcheck_report(mint):
    """Full RugCheck report for a mint. Throttled to one call per 2s."""
    global _last_rugcheck_call
    gap = time.time() - _last_rugcheck_call
    if gap < config.RUGCHECK_MIN_GAP_SECONDS:
        time.sleep(config.RUGCHECK_MIN_GAP_SECONDS - gap)
    data = _get(f"{config.RUGCHECK_BASE}/v1/tokens/{mint}/report")
    _last_rugcheck_call = time.time()
    return data


def rugcheck_dev_pct(report):
    """Creator's share of supply in percent, or None when unverifiable.

    Conservative: the highest of (a) the creator's summed share in
    topHolders, (b) creatorBalance interpreted as a percent, or (c)
    creatorBalance as a raw amount divided by total supply.
    """
    creator = report.get("creator")
    supply = (report.get("token") or {}).get("supply")
    candidates = []
    if creator:
        matched = sum(
            float(h.get("pct") or 0)
            for h in (report.get("topHolders") or [])
            if h.get("address") == creator or h.get("owner") == creator
        )
        candidates.append(matched)
    cb = report.get("creatorBalance")
    if isinstance(cb, (int, float)):
        if cb > 100 and supply:
            candidates.append(cb / float(supply) * 100.0)
        elif 0 <= cb <= 100:
            candidates.append(float(cb))
    if not candidates:
        return None
    return max(candidates)


def rugcheck_top10_ex(report, exclude_addresses):
    """Top-10 holder pct excluding curve/pool accounts.

    Excludes the given addresses plus any address RugCheck itself labels
    as a locker/pool/curve account.
    """
    from .scoring import top10_ex_curve

    known = report.get("knownAccounts") or {}
    labelled = {
        addr for addr, info in known.items()
        if (info or {}).get("type") in ("LOCKER", "POOL", "CURVE", "BONDING_CURVE")
    }
    holders = [
        {"address": h.get("address"), "pct": h.get("pct")}
        for h in (report.get("topHolders") or [])
    ]
    pct, _n = top10_ex_curve(holders, set(exclude_addresses or []) | labelled)
    return pct


def rugcheck_authorities(report):
    """(mint_authority, freeze_authority) as base58 strings or None.

    Null in the report means the authority is revoked / never set.
    """
    token = report.get("token") or {}
    mint_auth = report.get("mintAuthority", token.get("mintAuthority"))
    freeze_auth = report.get("freezeAuthority", token.get("freezeAuthority"))
    return mint_auth, freeze_auth


def rugcheck_creator_clean(report, mint):
    """True if the creator has no prior rugged/dead token, False if they
    do, None when the history cannot be verified.

    An empty creator-token list means no prior tokens at all -> clean.
    The current token itself is excluded from the "prior" list.
    """
    tokens = report.get("creatorTokens")
    if tokens is None:
        return None
    prior = [t for t in tokens if (t or {}).get("mint") != mint]
    if not prior:
        return True
    for t in prior:
        if (t or {}).get("rugged") is True:
            return False
    # Entries exist but carry no rugged flag we can read: unverifiable.
    return None


# ---------------------------------------------------------------------------
# Jupiter lite-api — price fallback / executable-price check (free, no key)
# ---------------------------------------------------------------------------

def jupiter_price(mint):
    """Price v3 for a mint. Returns {"usd_price", "liquidity"} or None."""
    data = _get(f"{config.JUPITER_LITE_BASE}/price/v3", params={"ids": mint})
    entry = data.get(mint)
    if not entry:
        return None
    price = entry.get("usdPrice")
    if price is None:
        return None
    return {"usd_price": float(price), "liquidity": entry.get("liquidity")}


# ---------------------------------------------------------------------------
# On-chain reads — Helius (key) with public-RPC fallback (no key)
# ---------------------------------------------------------------------------

def _decode_mint_account(raw: bytes):
    """Decode an 82-byte SPL mint account.

    Layout: mint_authority COption (4B flag + 32B), supply u64, decimals u8,
    is_initialized u8, freeze_authority COption (4B flag + 32B).
    Returns (mint_authority, freeze_authority) as base58 or None.
    """
    if len(raw) < 82:
        raise ProviderError(f"mint account too short: {len(raw)} bytes")
    ma_flag = int.from_bytes(raw[0:4], "little")
    mint_auth = _b58encode(raw[4:36]) if ma_flag else None
    fa_off = 4 + 32 + 8 + 1 + 1
    fa_flag = int.from_bytes(raw[fa_off:fa_off + 4], "little")
    freeze_auth = _b58encode(raw[fa_off + 4:fa_off + 36]) if fa_flag else None
    return mint_auth, freeze_auth


def solana_mint_authorities(mint):
    """Mint/freeze authority via the public Solana RPC (no key)."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
               "params": [mint, {"encoding": "base64"}]}
    data = _post_json(config.SOLANA_PUBLIC_RPC_URL, payload)
    value = (data.get("result") or {}).get("value")
    if not value:
        raise ProviderError("mint account not found on Solana RPC")
    raw = base64.b64decode(value["data"][0])
    return _decode_mint_account(raw)


def helius_get_asset(mint, api_key):
    """DAS getAsset for a mint via Helius. Returns token_info dict."""
    url = f"{config.HELIUS_RPC_URL}/?api-key={api_key}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getAsset",
               "params": {"id": mint}}
    data = _post_json(url, payload)
    result = data.get("result") or {}
    if "error" in data:
        raise ProviderError(f"Helius getAsset error: {data['error']}")
    return result.get("token_info") or {}


def mint_authorities(mint, helius_api_key=None):
    """Mint/freeze authority, Helius first, public RPC as fallback.

    Returns (mint_authority, freeze_authority, source).
    """
    if helius_api_key:
        try:
            info = helius_get_asset(mint, helius_api_key)
            return info.get("mint_authority"), info.get("freeze_authority"), "helius"
        except ProviderError:
            pass  # fall through to the public RPC
    mint_auth, freeze_auth = solana_mint_authorities(mint)
    return mint_auth, freeze_auth, "solana_public_rpc"


def token_account_balance_ui(account, helius_api_key=None):
    """Token balance (whole tokens) of a token account, or None."""
    url = config.SOLANA_PUBLIC_RPC_URL
    if helius_api_key:
        url = f"{config.HELIUS_RPC_URL}/?api-key={helius_api_key}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountBalance",
               "params": [account]}
    data = _post_json(url, payload)
    value = (data.get("result") or {}).get("value")
    if not value:
        return None
    ui = value.get("uiAmount")
    return float(ui) if ui is not None else None


def curve_token_account(bonding_curve_key, mint, helius_api_key=None):
    """The token account holding the bonding curve's supply, or None.

    The PumpPortal event carries the curve PDA but not its token
    account, so it is resolved here via getTokenAccountsByOwner.
    Returns the account address (base58 string).
    """
    if not bonding_curve_key or not mint:
        return None
    url = config.SOLANA_PUBLIC_RPC_URL
    if helius_api_key:
        url = f"{config.HELIUS_RPC_URL}/?api-key={helius_api_key}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
               "params": [bonding_curve_key, {"mint": mint},
                          {"encoding": "jsonParsed"}]}
    data = _post_json(url, payload)
    accounts = (data.get("result") or {}).get("value") or []
    if not accounts:
        return None
    return accounts[0].get("pubkey")


def token_supply_ui(mint, helius_api_key=None):
    """Total supply of a mint in whole tokens, or None."""
    url = config.SOLANA_PUBLIC_RPC_URL
    if helius_api_key:
        url = f"{config.HELIUS_RPC_URL}/?api-key={helius_api_key}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply",
               "params": [mint]}
    data = _post_json(url, payload)
    value = (data.get("result") or {}).get("value")
    if not value:
        return None
    ui = value.get("uiAmount")
    return float(ui) if ui is not None else None


def curve_progress_pct(bonding_curve_key, mint, helius_api_key=None,
                       curve_account=None, total_supply=None):
    """Bonding-curve progress in percent, or None when unverifiable.

    Progress = 1 - (curve token balance / total supply). The curve
    starts holding the token's full supply (verified live 2026-11-20),
    so progress is the fraction of supply bought out of the curve.
    The curve token account is resolved via getTokenAccountsByOwner
    unless `curve_account` is passed in directly; total supply comes
    from the caller (RugCheck token.supply) or a getTokenSupply call.
    """
    account = curve_account or curve_token_account(
        bonding_curve_key, mint, helius_api_key
    )
    if not account:
        return None
    balance = token_account_balance_ui(account, helius_api_key)
    if balance is None:
        return None
    supply = total_supply
    if supply is None:
        supply = token_supply_ui(mint, helius_api_key)
    if not supply:
        return None
    progress = (1.0 - balance / supply) * 100.0
    return max(0.0, min(100.0, progress))


# ---------------------------------------------------------------------------
# Frozen entry-price source (design section 4)
# ---------------------------------------------------------------------------

def entry_price(mint, graduated):
    """Entry price + frozen source. Graduated -> DexScreener pair price;
    still on the curve -> Jupiter lite-api price. Never guessed.

    Returns (price_usd, source) or (None, None).
    """
    if graduated:
        pair = dexscreener_best_pair(mint)
        if pair and pair["price_usd"] is not None:
            return pair["price_usd"], "dexscreener"
        return None, None
    quote = jupiter_price(mint)
    if quote:
        return quote["usd_price"], "jupiter"
    return None, None


def current_price(mint, entry_source):
    """Current price from the same source the call entered on."""
    if entry_source == "dexscreener":
        pair = dexscreener_best_pair(mint)
        if pair and pair["price_usd"] is not None:
            return pair["usd_price"], "dexscreener"
        return None, None
    quote = jupiter_price(mint)
    if quote:
        return quote["usd_price"], "jupiter"
    return None, None
