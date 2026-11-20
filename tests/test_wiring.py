"""Deploy guards — the two lessons from the 2026-11-20 deploy.

The Stage 1 suite passed 49 tests while two deploy-blocking bugs
shipped: a lifespan handler never attached to the FastAPI app, and
PRAGMA statements Turso's Hrana protocol rejects. These tests pin
both fixes at the level where they failed: boot the REAL app and hit
/health, and scan the runtime sources for PRAGMA.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

import tracker  # noqa: E402
from memeline import capture, config, db  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_no_pragma_in_runtime_sources():
    """No PRAGMA statement may exist in any file that runs against the
    database. Turso's Hrana protocol rejects PRAGMA outright, so one
    stray line kills the deployed app at startup — exactly what
    happened on 2026-11-20 (tracker.py and capture.py)."""
    runtime_files = [os.path.join(ROOT, "tracker.py"),
                     os.path.join(ROOT, "capture.py"),
                     os.path.join(ROOT, "schema.sql")]
    runtime_files += [
        os.path.join(ROOT, "memeline", name)
        for name in sorted(os.listdir(os.path.join(ROOT, "memeline")))
        if name.endswith(".py")]
    assert runtime_files, "runtime file list came back empty"
    offenders = []
    for path in runtime_files:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if "PRAGMA" in content.upper():
            offenders.append(os.path.basename(path))
    assert offenders == [], f"PRAGMA found in runtime sources: {offenders}"


def _boot_client(monkeypatch, tmp_path, capture_enabled):
    db_path = str(tmp_path / "boot.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(config, "TURSO_DATABASE_URL", "")
    monkeypatch.setattr(config, "TURSO_AUTH_TOKEN", "")
    monkeypatch.setattr(config, "CAPTURE_ENABLED", capture_enabled)
    started = {}

    async def fake_feed(on_launch, on_migration, on_gap, on_reconnect=None,
                        stop_event=None):
        started["feed"] = True
        if on_reconnect is not None:
            on_reconnect()
        await stop_event.wait()

    monkeypatch.setattr(capture, "select_feed_runner", lambda: fake_feed)
    return db_path, started


def test_app_boots_lifespan_creates_tables_and_health(monkeypatch, tmp_path):
    """Boot the REAL FastAPI app against a local SQLite DB and hit
    /health: the lifespan must run, create the tables, and start the
    capture task. This is the test that would have caught the
    unattached-lifespan bug before deploy."""
    db_path, started = _boot_client(monkeypatch, tmp_path, True)
    with TestClient(tracker.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["capture_enabled"] is True
        assert body["capture_running"] is True
        assert started.get("feed") is True
    conn = db.connect(database_path=db_path)
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"raw_launches", "evaluations", "paper_calls", "grades",
            "gaps", "meta"} <= tables
    meta = db.fetchone_dict(conn, "SELECT * FROM meta WHERE key='started_at'")
    assert meta is not None


def test_app_boots_with_capture_disabled(monkeypatch, tmp_path):
    """Tables are still created by the lifespan when capture is off;
    /health reports capture_running false instead of crashing."""
    db_path, started = _boot_client(monkeypatch, tmp_path, False)
    with TestClient(tracker.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["capture_enabled"] is False
        assert body["capture_running"] is False
        assert started.get("feed") is None
    conn = db.connect(database_path=db_path)
    n = db.fetchone_dict(
        conn, "SELECT COUNT(*) AS n FROM sqlite_master "
              "WHERE type='table' AND name='raw_launches'")["n"]
    assert n == 1
