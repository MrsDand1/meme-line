"""PumpPortal event parsing tests — against a real event fetched live
2026-11-20 (see tests/fixtures/pumpportal-new-token.json)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline.pumpportal import parse_migration_event, parse_new_token_event  # noqa: E402
from memeline.scoring import prefilter  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def test_parse_real_new_token_event():
    ev = parse_new_token_event(_load("pumpportal-new-token.json"))
    assert ev is not None
    assert ev["mint"] == "9112ktmpXnFYbULPqkg1J9PhiHHEVDE1uBJ6ayWapump"
    assert ev["name"] == "Flyshake"
    assert ev["symbol"] == "FLYSHAKE"
    # The creator is traderPublicKey — there is no `creator` field.
    assert ev["creator"] == "DkFDkr95h7A19HMqiF5g8A4YXkjxp2kmeCibJ6MLbpdr"
    assert ev["bonding_curve_key"] == "46GCWN3GxVejz1nFLPT6xYm1oTo9gUogG4aM8qX46Ro6"
    assert ev["pool"] == "pump"
    # Virtual reserves: vTokens + initialBuy == 1,073,000,000 exactly.
    assert abs(ev["v_tokens_in_curve"] + 66_285_714.223523 - 1_073_000_000) < 1


def test_subscribe_confirmation_is_ignored():
    assert parse_new_token_event(
        {"message": "Successfully subscribed to token creation events."}
    ) is None
    assert parse_new_token_event({}) is None
    assert parse_new_token_event("not a dict") is None


def test_prefilter_pool_must_be_pump():
    base = {"mint": "M", "name": "Moon", "symbol": "MOON"}
    assert prefilter({**base, "pool": "pump"})[0] is True
    assert prefilter({**base})[0] is True  # pool absent: accept
    ok, reason = prefilter({**base, "pool": "raydium"})
    assert ok is False
    assert "not a pump.fun launch" in reason


def test_parse_migration_event():
    ev = parse_migration_event({"mint": "M", "poolAddress": "POOL1"})
    assert ev == {"mint": "M", "pool_address": "POOL1", "raw": {"mint": "M", "poolAddress": "POOL1"}}
    assert parse_migration_event({"no_mint": True}) is None
