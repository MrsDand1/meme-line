"""Provider parsing tests — offline, against JSON fetched live on
2026-11-20 from a real token (see tests/fixtures). No network calls.
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline import providers  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def test_rugcheck_report_shape():
    rep = _load("rugcheck-report.json")
    assert rep["score_normalised"] == 34
    assert rep["creator"] == "C7ZA7qbzjgwoSVzMrvF6hbrUmDtHXQUtpmYX9Zx77wPE"
    assert len(rep["topHolders"]) == 20
    assert rep["token"]["supply"] == 1_000_000_000_000_000


def test_rugcheck_dev_pct():
    rep = _load("rugcheck-report.json")
    # Creator holds 0 (creatorBalance 0, not in top holders).
    assert providers.rugcheck_dev_pct(rep) == 0.0


def test_rugcheck_top10_excludes_curve():
    rep = _load("rugcheck-report.json")
    # Top holder (26.43%) is a pool/locker-style account; excluding it,
    # the next ten sum to ~21.86%.
    top = rep["topHolders"][0]["address"]
    pct = providers.rugcheck_top10_ex(rep, [top])
    assert abs(pct - 21.86) < 0.05


def test_rugcheck_authorities_revoked():
    rep = _load("rugcheck-report.json")
    mint_auth, freeze_auth = providers.rugcheck_authorities(rep)
    assert mint_auth is None
    assert freeze_auth is None


def test_rugcheck_creator_clean_no_prior_tokens():
    rep = _load("rugcheck-report.json")
    # The fixture report carries creatorTokens: null (no creator-token
    # data at all) -> unverifiable -> None, never a pass.
    assert rep["creatorTokens"] is None
    assert providers.rugcheck_creator_clean(rep, rep["mint"]) is None
    # An explicitly empty list means no prior tokens -> clean.
    rep2 = dict(rep, creatorTokens=[])
    assert providers.rugcheck_creator_clean(rep2, rep["mint"]) is True
    # A prior token flagged rugged -> not clean.
    rep3 = dict(rep, creatorTokens=[{"mint": "OTHER", "rugged": True}])
    assert providers.rugcheck_creator_clean(rep3, rep["mint"]) is False


def test_rugcheck_danger_flags():
    rep = _load("rugcheck-report.json")
    danger = [(r or {}).get("level") == "danger" for r in rep.get("risks") or []]
    assert any(danger)  # 'Low Liquidity' danger on this fixture token


def test_dexscreener_best_pair():
    data = _load("dexscreener-token.json")
    pairs = data["pairs"]
    assert len(pairs) == 1
    # dexscreener_best_pair hits the network; test the parsing logic
    # directly on the fixture pair instead.
    p = pairs[0]
    assert p["dexId"] == "meteoradbc"
    assert p.get("liquidity") is None  # DBC pairs carry no liquidity key
    info = p["info"]
    assert any(s["type"] == "twitter" for s in info["socials"])
    assert any(s["type"] == "telegram" for s in info["socials"])
    assert info["websites"][0]["url"] == "https://nacreshield.xyz/"


def test_jupiter_price_fixture():
    data = _load("jupiter-price.json")
    mint = "FoVdh9nrKp2D35bBU2jS3TML4vQWMGk16HTYxoUA6zKv"
    entry = data[mint]
    assert entry["usdPrice"] > 0
    assert entry["liquidity"] > 0
    assert entry["launchpad"] == "met-dbc"


def test_mint_account_decode_both_revoked():
    raw = _load("solana-mint-account.json")
    value = raw["result"]["value"]
    decoded = base64.b64decode(value["data"][0])
    assert len(decoded) == 82
    assert value["owner"] == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    mint_auth, freeze_auth = providers._decode_mint_account(decoded)
    assert mint_auth is None
    assert freeze_auth is None


def test_b58encode_vectors():
    assert providers._b58encode(b"\x00" * 32) == "1" * 32
    assert providers._b58encode(b"\x00" * 31 + b"\x01") == "1" * 31 + "2"
    # Round-trip: decode the fixture mint account's authority bytes is
    # covered above; here check a known address encodes back.
    # "11111111111111111111111111111111" is 32 zero bytes.
    assert providers._b58encode(bytes.fromhex("00" * 32)) == "1" * 32


def test_curve_progress_math(monkeypatch):
    """Progress = 1 - balance/total_supply, with the curve account
    resolved. A fresh token at 60% sold reads 60%."""
    monkeypatch.setattr(providers, "curve_token_account",
                        lambda *a, **k: "CURVEACCT")
    monkeypatch.setattr(providers, "token_account_balance_ui",
                        lambda *a, **k: 400_000_000.0)
    pct = providers.curve_progress_pct("CURVEKEY", "MINT",
                                       total_supply=1_000_000_000.0)
    assert abs(pct - 60.0) < 1e-9
    # No curve account at all -> unknown, never a pass.
    monkeypatch.setattr(providers, "curve_token_account",
                        lambda *a, **k: None)
    assert providers.curve_progress_pct("CURVEKEY", "MINT") is None
