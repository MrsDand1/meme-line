"""The Meme Line — PumpPortal WebSocket client (design section 2).

Primary launch feed: wss://pumpportal.fun/api/data, free, no key.
ONE connection only — multiple connections trigger an hourly ban.
Reconnects with backoff; every disconnect is logged as a gap so a
missed evaluation window is visible, never silent.

In this sandbox the egress proxy must be used for the WebSocket
handshake (read from the environment at runtime, never logged or
stored). On Render there is no proxy and the client connects directly.
"""
import asyncio
import json
import os
import time

import websockets

from . import config


def _proxy_from_env():
    """Proxy URL from the environment, if one is set. Never logged."""
    return os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or None


def parse_new_token_event(msg):
    """Normalize a PumpPortal new-token event. Returns None if unusable.

    Real event shape (verified live 2026-11-20): the creator is
    `traderPublicKey` (there is no `creator` field), `txType` is
    "create", `pool` is "pump". There is NO associated-curve key in
    the event — the curve token account is resolved at evaluation time
    via RPC (see providers.curve_token_account). The first message
    after subscribing is {"message": "Successfully subscribed ..."}
    and is ignored (no mint).
    """
    if not isinstance(msg, dict):
        return None
    mint = msg.get("mint")
    if not mint:
        return None
    return {
        "mint": mint,
        "name": msg.get("name") or "",
        "symbol": msg.get("symbol") or "",
        "uri": msg.get("uri") or "",
        "creator": msg.get("traderPublicKey") or msg.get("creator") or "",
        "bonding_curve_key": msg.get("bondingCurveKey") or "",
        "associated_curve_key": msg.get("associatedBondingCurveKey") or "",
        "pool": msg.get("pool") or "",
        "v_tokens_in_curve": msg.get("vTokensInBondingCurve"),
        "v_sol_in_curve": msg.get("vSolInBondingCurve"),
        "market_cap_sol": msg.get("marketCapSol"),
        "raw": msg,
    }


def parse_migration_event(msg):
    """Normalize a PumpPortal migration (graduation) event."""
    if not isinstance(msg, dict):
        return None
    mint = msg.get("mint")
    if not mint:
        return None
    return {
        "mint": mint,
        "pool_address": msg.get("poolAddress") or msg.get("raydiumPool") or "",
        "raw": msg,
    }


async def run_feed(on_launch, on_migration, on_gap, on_reconnect=None,
               stop_event=None):
    """Run the feed forever (until stop_event is set).

    on_launch(event_dict), on_migration(event_dict) are sync callbacks.
    on_gap(started_at, reason) is called on every disconnect; on_reconnect
    is called after each (re)connection, so the caller can close the gap.
    Reconnects with backoff from config.WS_RECONNECT_BACKOFF.
    """
    backoff_idx = 0
    while stop_event is None or not stop_event.is_set():
        try:
            proxy = _proxy_from_env()
            kwargs = {"max_size": 10 * 1024 * 1024, "open_timeout": 30}
            if proxy:
                kwargs["proxy"] = proxy
            async with websockets.connect(config.PUMPPORTAL_WS_URL, **kwargs) as ws:
                backoff_idx = 0
                if on_reconnect is not None:
                    on_reconnect()
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                while stop_event is None or not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                    except asyncio.TimeoutError:
                        continue  # idle keep-alive; connection still open
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    tx_type = msg.get("txType")
                    if tx_type == "create":
                        ev = parse_new_token_event(msg)
                        if ev:
                            on_launch(ev)
                    elif tx_type == "migrate":
                        ev = parse_migration_event(msg)
                        if ev:
                            on_migration(ev)
                    else:
                        # Some events arrive without txType; treat a message
                        # with mint+name+symbol as a launch.
                        ev = parse_new_token_event(msg)
                        if ev and ev["name"] and ev["symbol"]:
                            on_launch(ev)
        except Exception as e:  # noqa: BLE001 - reconnect on any failure
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


async def _wait_stop(stop_event):
    if stop_event is None:
        await asyncio.sleep(3600)
        return
    await stop_event.wait()
