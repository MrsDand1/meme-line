"""Capture-loop tests: intake, evaluation windows, signal -> call.

Provider calls are mocked; the DB is a local temp file. No network.
"""
import os
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline import capture, db  # noqa: E402


def _fresh_db():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = db.connect(database_path=path)
    db.init_db(conn)
    return conn


def _launch(mint="MINT1", launched_at=None, **kw):
    ev = {
        "mint": mint,
        "name": "Moon",
        "symbol": "MOON",
        "uri": "",
        "creator": "CREATOR1",
        "bonding_curve_key": "BC1",
        "associated_curve_key": "AC1",
        "raw": {"mint": mint},
    }
    ev.update(kw)
    return ev


def _passing_evidence():
    return {
        "dev_pct": 3.0, "top10_pct_ex": 24.0, "rugcheck_score": 18,
        "rugcheck_danger": False, "graduated": False, "liquidity_usd": None,
        "curve_progress_pct": 62.0, "has_socials": True, "creator_clean": True,
        "freeze_authority": None, "creator": "CREATOR1", "has_market": True,
    }


def test_store_launch_dedupes():
    conn = _fresh_db()
    capture.store_launch(conn, _launch())
    capture.store_launch(conn, _launch())
    n = db.fetchone_dict(conn, "SELECT COUNT(*) AS n FROM raw_launches")["n"]
    assert n == 1


def test_store_migration_marks_graduation():
    conn = _fresh_db()
    capture.store_launch(conn, _launch())
    capture.store_migration(conn, {"mint": "MINT1", "pool_address": "POOL1"})
    row = db.fetchone_dict(conn, "SELECT * FROM raw_launches WHERE mint='MINT1'")
    assert row["graduated_at"] is not None
    assert row["pool_address"] == "POOL1"


def test_too_young_not_evaluated():
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch())
    launch = db.fetchone_dict(conn, "SELECT * FROM raw_launches")
    assert capture.evaluate_launch(conn, launch, now) is None
    assert db.fetchone_dict(conn, "SELECT COUNT(*) AS n FROM evaluations")["n"] == 0


def test_stale_launch_is_skipped_not_backfilled():
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch())
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='MINT1'",
                 (now - 3600,))
    conn.commit()
    launch = db.fetchone_dict(conn, "SELECT * FROM raw_launches")
    result = capture.evaluate_launch(conn, launch, now)
    assert result["status"] == "SKIPPED"
    row = db.fetchone_dict(conn, "SELECT * FROM evaluations WHERE mint='MINT1'")
    assert row["status"] == "SKIPPED"
    assert row["score"] is None


def test_prefilter_failure_is_skipped():
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch(symbol=""))
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='MINT1'",
                 (now - 600,))
    conn.commit()
    launch = db.fetchone_dict(conn, "SELECT * FROM raw_launches")
    result = capture.evaluate_launch(conn, launch, now)
    assert result["status"] == "SKIPPED"
    assert "prefilter" in result["note"]


def test_signal_opens_call_and_posts(monkeypatch):
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch())
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='MINT1'",
                 (now - 600,))
    conn.commit()
    launch = db.fetchone_dict(conn, "SELECT * FROM raw_launches")
    posted = []
    monkeypatch.setattr(capture, "gather_evidence",
                        lambda c, l: _passing_evidence())
    monkeypatch.setattr("memeline.providers.entry_price",
                        lambda mint, graduated: (0.0001, "jupiter"))
    monkeypatch.setattr(capture.discord, "post_signal",
                        lambda url, call, ev: posted.append(call) or True)
    result = capture.evaluate_launch(conn, launch, now)
    assert result["signal"] is True
    assert result["score"] == 9
    assert result["call_id"] == 1
    assert len(posted) == 1
    call = db.fetchone_dict(conn, "SELECT * FROM paper_calls WHERE id = 1")
    assert call["status"] == "OPEN"
    assert call["entry_price"] == 0.0001
    assert call["entry_source"] == "jupiter"


def test_signal_without_entry_price_opens_nothing(monkeypatch):
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch())
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='MINT1'",
                 (now - 600,))
    conn.commit()
    launch = db.fetchone_dict(conn, "SELECT * FROM raw_launches")
    monkeypatch.setattr(capture, "gather_evidence",
                        lambda c, l: _passing_evidence())
    monkeypatch.setattr("memeline.providers.entry_price",
                        lambda mint, graduated: (None, None))
    result = capture.evaluate_launch(conn, launch, now)
    assert result["signal"] is True
    assert "no entry price" in result["note"]
    assert db.fetchone_dict(conn, "SELECT COUNT(*) AS n FROM paper_calls")["n"] == 0


def test_no_signal_opens_nothing(monkeypatch):
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch())
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='MINT1'",
                 (now - 600,))
    conn.commit()
    launch = db.fetchone_dict(conn, "SELECT * FROM raw_launches")
    weak = _passing_evidence()
    weak["dev_pct"] = 15.0  # check 1 fails -> score 7... still a signal
    weak["curve_progress_pct"] = 10.0  # -1 -> 6, no signal
    monkeypatch.setattr(capture, "gather_evidence", lambda c, l: weak)
    result = capture.evaluate_launch(conn, launch, now)
    assert result["signal"] is False
    assert result["score"] == 6
    assert db.fetchone_dict(conn, "SELECT COUNT(*) AS n FROM paper_calls")["n"] == 0


def test_evaluate_due_picks_only_due_launches(monkeypatch):
    conn = _fresh_db()
    now = time.time()
    capture.store_launch(conn, _launch(mint="DUE"))
    capture.store_launch(conn, _launch(mint="YOUNG"))
    capture.store_launch(conn, _launch(mint="STALE"))
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='DUE'",
                 (now - 600,))
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='YOUNG'",
                 (now - 60,))
    conn.execute("UPDATE raw_launches SET launched_at = ? WHERE mint='STALE'",
                 (now - 3600,))
    conn.commit()
    monkeypatch.setattr(capture, "gather_evidence",
                        lambda c, l: _passing_evidence())
    monkeypatch.setattr("memeline.providers.entry_price",
                        lambda mint, graduated: (0.0001, "jupiter"))
    monkeypatch.setattr(capture.discord, "post_signal",
                        lambda url, call, ev: True)
    results = capture.evaluate_due(conn, now)
    mints = {r["mint"] for r in results}
    assert "DUE" in mints
    assert "STALE" in mints  # stale gets a SKIPPED row
    assert "YOUNG" not in mints
    stale = db.fetchone_dict(
        conn, "SELECT * FROM evaluations WHERE mint='STALE'")
    assert stale["status"] == "SKIPPED"


def _fixture_report():
    import json
    path = os.path.join(os.path.dirname(__file__), "fixtures",
                        "rugcheck-report.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_top10_unknown_when_curve_unresolved(monkeypatch):
    """Live regression (2026-11-20): with the curve account unresolvable,
    RugCheck's top-10 INCLUDES the curve (~97%) and reads 100% — that
    must be recorded as unknown, never as a veto on the curve's own
    holdings. When graduated there is no curve, so the number stands."""
    import json
    from memeline import providers
    report = _fixture_report()
    mint = report["mint"]
    launch = {"mint": mint, "creator": report["creator"], "uri": "",
              "bonding_curve_key": "CURVEKEY", "name": "N", "symbol": "NAC"}

    def fake_report(m):
        return report

    monkeypatch.setattr(providers, "rugcheck_report", fake_report)
    monkeypatch.setattr(providers, "mint_authorities",
                        lambda *a, **k: (None, None, "test"))
    monkeypatch.setattr(capture, "_fetch_metadata_socials",
                        lambda uri: (False, False))

    # Case A: still on the curve, curve account unresolvable -> unknown.
    monkeypatch.setattr(providers, "curve_token_account",
                        lambda *a, **k: None)
    monkeypatch.setattr(providers, "dexscreener_best_pair",
                        lambda m: None)
    monkeypatch.setattr(providers, "jupiter_price", lambda m: None)
    ev = capture.gather_evidence(None, launch)
    assert ev["top10_pct_ex"] is None
    assert ev["graduated"] is False

    # Case B: curve account resolves to the top holder -> excluded.
    top = report["topHolders"][0]["address"]
    monkeypatch.setattr(providers, "curve_token_account",
                        lambda *a, **k: top)
    ev = capture.gather_evidence(None, launch)
    assert ev["top10_pct_ex"] is not None
    assert abs(ev["top10_pct_ex"] - 21.86) < 0.1

    # Case C: graduated, no curve to exclude -> number stands as-is.
    monkeypatch.setattr(providers, "curve_token_account",
                        lambda *a, **k: None)
    monkeypatch.setattr(providers, "dexscreener_best_pair", lambda m: {
        "price_usd": 0.001, "liquidity_usd": 50_000.0, "dex_id": "raydium",
        "pair_address": "P", "fdv": 100_000.0, "has_socials": False,
        "socials": [], "websites": []})
    ev = capture.gather_evidence(None, launch)
    assert ev["graduated"] is True
    assert ev["top10_pct_ex"] is not None
    assert abs(ev["top10_pct_ex"] - 46.71) < 0.1
