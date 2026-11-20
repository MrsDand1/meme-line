"""DB round-trip tests (local libsql file)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memeline import db  # noqa: E402


def _fresh_db():
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = db.connect(database_path=path)
    db.init_db(conn)
    return conn


def test_schema_creates_all_tables():
    conn = _fresh_db()
    tables = {r["name"] for r in db.fetchall_dicts(
        conn, "SELECT name FROM sqlite_master WHERE type='table'")}
    for expected in ("raw_launches", "evaluations", "paper_calls",
                     "grades", "gaps", "meta"):
        assert expected in tables


def test_launch_and_evaluation_round_trip():
    conn = _fresh_db()
    conn.execute(
        """INSERT INTO raw_launches (mint, name, symbol, creator,
           bonding_curve_key, associated_curve_key, launched_at, raw_json)
           VALUES ('M1', 'Moon', 'MOON', 'C1', 'BC1', 'AC1', 1000.0, '{}')"""
    )
    conn.execute(
        """INSERT INTO evaluations (mint, evaluated_at, age_seconds, score,
           signal, status, breakdown_json, vetoes_json, unknowns_json)
           VALUES ('M1', 1600.0, 600.0, 9, 1, 'EVALUATED', '[]', '[]', '[]')"""
    )
    conn.commit()
    row = db.fetchone_dict(
        conn, "SELECT * FROM evaluations WHERE mint = 'M1'")
    assert row["score"] == 9
    assert row["signal"] == 1
    assert row["status"] == "EVALUATED"


def test_init_db_is_idempotent():
    conn = _fresh_db()
    db.init_db(conn)  # second run must not raise
    db.init_db(conn)
    assert db.fetchone_dict(conn, "SELECT COUNT(*) AS n FROM raw_launches")["n"] == 0
