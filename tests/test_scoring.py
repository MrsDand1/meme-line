"""Tests for the frozen v1 scoring rule (design section 3).

Includes the design's own worked example: MOONCAT scores 9 and signals.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline.scoring import prefilter, score, top10_ex_curve  # noqa: E402


def _mooncat_evidence(**overrides):
    ev = {
        "dev_pct": 3.0,
        "top10_pct_ex": 24.0,
        "rugcheck_score": 18,
        "rugcheck_danger": False,
        "graduated": False,
        "liquidity_usd": None,
        "curve_progress_pct": 62.0,
        "has_socials": True,
        "creator_clean": True,
        "freeze_authority": None,
        "creator": "Creator111111111111111111111111111111111",
        "has_market": True,
    }
    ev.update(overrides)
    return ev


def test_mooncat_worked_example():
    """Design section 3 worked example: MOONCAT scores 9 -> SIGNAL."""
    result = score(_mooncat_evidence())
    assert result["score"] == 9, result["checks"]
    assert result["signal"] is True
    assert result["vetoes"] == []


def test_score_6_is_not_a_signal():
    ev = _mooncat_evidence(has_socials=False)  # -1 -> 8... still a signal
    assert score(ev)["signal"] is True
    ev = _mooncat_evidence(has_socials=False, creator_clean=False)  # 7
    assert score(ev)["score"] == 7
    assert score(ev)["signal"] is True
    ev = _mooncat_evidence(has_socials=False, creator_clean=False,
                           curve_progress_pct=40.0)  # 6
    assert score(ev)["score"] == 6
    assert score(ev)["signal"] is False


def test_dev_over_20_vetoes_at_any_score():
    result = score(_mooncat_evidence(dev_pct=25.0))
    assert result["score"] == 7  # check 1 fails too, but the veto is the point
    assert result["signal"] is False
    assert any("dev holds 25.00%" in v for v in result["vetoes"])


def test_top10_over_50_vetoes():
    result = score(_mooncat_evidence(top10_pct_ex=55.0))
    assert result["signal"] is False
    assert any("top-10 hold 55.00%" in v for v in result["vetoes"])


def test_freeze_held_by_creator_vetoes():
    creator = "Creator111111111111111111111111111111111"
    result = score(_mooncat_evidence(freeze_authority=creator, creator=creator))
    assert result["signal"] is False
    assert any("freeze authority" in v for v in result["vetoes"])


def test_freeze_held_by_program_does_not_veto():
    # pump.fun tokens: freeze authority (if any) sits with the program,
    # not the creator. That is not a veto.
    result = score(_mooncat_evidence(
        freeze_authority="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"))
    assert result["signal"] is True
    assert result["vetoes"] == []


def test_zero_liquidity_vetoes():
    result = score(_mooncat_evidence(has_market=False))
    assert result["signal"] is False
    assert any("zero liquidity" in v for v in result["vetoes"])


def test_unknown_evidence_scores_zero_never_a_pass():
    ev = _mooncat_evidence(dev_pct=None, top10_pct_ex=None,
                           rugcheck_score=None, creator_clean=None,
                           curve_progress_pct=None)
    result = score(ev)
    # Only check 5 (socials) can pass: 1 point, no signal.
    assert result["score"] == 1
    assert result["signal"] is False
    assert set(result["unknowns"]) == {1, 2, 3, "4a", 6}


def test_rugcheck_danger_flag_fails_check_3():
    result = score(_mooncat_evidence(rugcheck_danger=True))
    assert result["score"] == 7  # 9 - 2
    assert result["signal"] is True  # still signals: check 3 is only 2 pts
    assert result["vetoes"] == []


def test_graduated_liquidity_path():
    ev = _mooncat_evidence(graduated=True, liquidity_usd=50_000.0,
                           curve_progress_pct=None)
    result = score(ev)
    assert result["score"] == 10
    assert result["signal"] is True
    ev = _mooncat_evidence(graduated=True, liquidity_usd=5_000.0,
                           curve_progress_pct=None)
    result = score(ev)
    assert result["score"] == 8  # check 4 fails, 4a does not apply
    assert result["signal"] is True
    ev = _mooncat_evidence(graduated=True, liquidity_usd=None,
                           curve_progress_pct=None)
    result = score(ev)
    assert result["score"] == 8
    assert 4 in result["unknowns"]


def test_top10_excludes_curve_and_pool_accounts():
    holders = [
        {"address": "CURVE", "pct": 80.0},
        {"address": "A", "pct": 5.0},
        {"address": "B", "pct": 4.0},
        {"address": "C", "pct": 3.0},
    ]
    pct, excluded = top10_ex_curve(holders, {"CURVE"})
    assert pct == 12.0
    assert excluded == 1


def test_prefilter():
    assert prefilter({"mint": "M", "name": "Moon", "symbol": "MOON"})[0] is True
    assert prefilter({"mint": "M", "name": "", "symbol": "MOON"})[0] is False
    assert prefilter({"mint": "M", "name": "Moon", "symbol": ""})[0] is False
    assert prefilter({"mint": "M", "name": "Moon", "symbol": "VERYLONGSYMBOL"})[0] is False
    assert prefilter({"mint": "", "name": "Moon", "symbol": "MOON"})[0] is False
    # 10-char symbol is the limit and passes.
    assert prefilter({"mint": "M", "name": "Moon", "symbol": "0123456789"})[0] is True
