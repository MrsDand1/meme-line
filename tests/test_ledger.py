"""Tests for the paper ledger (design section 4).

Grading math: TP +98%, SL -52%, flat at 24h -2% (all after the 2%
round-trip fee). Gate: 50 calls over >= 2 weeks, net positive.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline import db, ledger  # noqa: E402


def _fresh_db():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = db.connect(database_path=path)
    db.init_db(conn)
    return conn


def _open(conn, mint="MINT1", signal_at=None, entry=0.0001):
    return ledger.open_call(
        conn, mint, signal_at if signal_at is not None else time.time(),
        entry, "dexscreener", 9, [],
    )


def test_net_return_math():
    import pytest
    assert ledger.net_return(0.0001, 0.0002) == pytest.approx(0.98)
    assert ledger.net_return(0.0001, 0.00005) == pytest.approx(-0.52)
    assert ledger.net_return(0.0001, 0.0001) == pytest.approx(-0.02)
    assert ledger.net_return(0.0001, 0.00015) == pytest.approx(0.48)


def test_take_profit_grades_first():
    conn = _fresh_db()
    _open(conn, entry=0.0001)
    closed = ledger.grade_open_calls(conn, lambda m, s: (0.0002, "dexscreener"))
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TP"
    assert closed[0]["net_return"] == 0.98
    row = db.fetchone_dict(conn, "SELECT status FROM paper_calls WHERE id = 1")
    assert row["status"] == "CLOSED"


def test_stop_loss_grades():
    conn = _fresh_db()
    _open(conn, entry=0.0001)
    closed = ledger.grade_open_calls(conn, lambda m, s: (0.00005, "dexscreener"))
    assert closed[0]["exit_reason"] == "SL"
    assert closed[0]["net_return"] == -0.52


def test_time_stop_grades_at_24h_price():
    conn = _fresh_db()
    t0 = time.time() - 25 * 3600
    _open(conn, signal_at=t0, entry=0.0001)
    closed = ledger.grade_open_calls(
        conn, lambda m, s: (0.00012, "dexscreener"), now=time.time())
    assert closed[0]["exit_reason"] == "24h"
    assert abs(closed[0]["net_return"] - 0.18) < 1e-9


def test_no_price_at_time_stop_stays_open():
    conn = _fresh_db()
    t0 = time.time() - 25 * 3600
    _open(conn, signal_at=t0, entry=0.0001)
    closed = ledger.grade_open_calls(conn, lambda m, s: (None, None))
    assert closed == []
    row = db.fetchone_dict(conn, "SELECT status FROM paper_calls WHERE id = 1")
    assert row["status"] == "OPEN"


def test_below_tp_stays_open():
    conn = _fresh_db()
    _open(conn, entry=0.0001)
    closed = ledger.grade_open_calls(conn, lambda m, s: (0.00015, "dexscreener"))
    assert closed == []


def test_daily_cap_is_five():
    conn = _fresh_db()
    for i in range(5):
        _open(conn, mint=f"MINT{i}")
    try:
        _open(conn, mint="MINT5")
        raise AssertionError("6th call should have raised DailyCapReached")
    except ledger.DailyCapReached:
        pass


def test_gate_progress_statuses():
    conn = _fresh_db()
    t0 = time.time() - 15 * 86400  # 15 days ago: gate time condition met
    # 50 closed calls, net positive -> PASSED (one per day, so the
    # 5/day signal cap never trips)
    for i in range(50):
        cid = _open(conn, mint=f"M{i}", signal_at=t0 + i * 86400)
        conn.execute(
            "UPDATE paper_calls SET status='CLOSED', exit_price=0.0002,"
            " exit_reason='TP', exit_at=?, net_return=0.98 WHERE id=?",
            (t0 + 3600, cid),
        )
    conn.commit()
    ledger.recompute_grades(conn)
    g = ledger.gate_progress(conn)
    assert g["calls"] == 50
    assert g["wins"] == 50
    assert g["status"] == "PASSED"
    assert abs(g["cumulative_net"] - 49.0) < 1e-9

    # Same 50 calls but net negative -> FAILED
    conn.execute("UPDATE paper_calls SET net_return = -0.52")
    conn.commit()
    ledger.recompute_grades(conn)
    assert ledger.gate_progress(conn)["status"] == "FAILED"

    # Fewer than 50 calls -> IN_PROGRESS even with positive net
    conn.execute("DELETE FROM paper_calls WHERE id > 10")
    conn.commit()
    ledger.recompute_grades(conn)
    assert ledger.gate_progress(conn)["status"] == "IN_PROGRESS"


def test_grades_recomputed_from_calls_never_hand_edited():
    conn = _fresh_db()
    _open(conn, entry=0.0001)
    ledger.grade_open_calls(conn, lambda m, s: (0.0002, "dexscreener"))
    g = ledger.recompute_grades(conn)
    assert g == {"calls": 1, "wins": 1, "cumulative_net": 0.98, "avg_net": 0.98}
