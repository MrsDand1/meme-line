"""The Meme Line — public tracking API (paper only, never live trades).

Serves the public track record: latest signals, paper calls, gate
progress, feed health. Signals only — the bot never trades, never holds
a key, never touches money. Mirrors the BTC Oracle Pro tracker shape.
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from memeline import config, db, ledger
from memeline.capture import run_capture

_STARTED_AT = time.time()
_capture_task = None

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _conn():
    return db.connect()


@asynccontextmanager
async def lifespan(app):
    global _capture_task
    conn = _conn()
    db.init_db(conn)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('started_at', ?)",
        (str(_STARTED_AT),),
    )
    conn.commit()
    conn.close()
    if config.CAPTURE_ENABLED:
        _capture_task = asyncio.create_task(run_capture())
    yield
    if _capture_task is not None:
        _capture_task.cancel()


app = FastAPI(title="The Meme Line", version=config.VERSION)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/health")
def health():
    conn = _conn()
    try:
        last = db.fetchone_dict(
            conn, "SELECT MAX(launched_at) AS last_launch FROM raw_launches"
        )
        open_gaps = db.fetchone_dict(
            conn, "SELECT COUNT(*) AS n FROM gaps WHERE ended_at IS NULL"
        )
        return {
            "status": "ok",
            "version": config.VERSION,
            "now": time.time(),
            "uptime_seconds": round(time.time() - _STARTED_AT, 1),
            "capture_enabled": config.CAPTURE_ENABLED,
            "capture_running": _capture_task is not None
            and not _capture_task.done(),
            "last_launch_at": (last or {}).get("last_launch"),
            "open_gaps": (open_gaps or {}).get("n", 0),
        }
    finally:
        conn.close()


@app.get("/live")
def live():
    conn = _conn()
    try:
        gate = ledger.gate_progress(conn)
        signals = db.fetchall_dicts(
            conn,
            """SELECT e.*, l.name, l.symbol FROM evaluations e
               JOIN raw_launches l ON l.mint = e.mint
               WHERE e.signal = 1 ORDER BY e.evaluated_at DESC LIMIT 10""",
        )
        open_calls = db.fetchall_dicts(
            conn,
            """SELECT c.*, l.name, l.symbol FROM paper_calls c
               JOIN raw_launches l ON l.mint = c.mint
               WHERE c.status = 'OPEN' ORDER BY c.signal_at DESC""",
        )
        last = db.fetchone_dict(
            conn, "SELECT MAX(launched_at) AS last_launch FROM raw_launches"
        )
        return {
            "gate": gate,
            "latest_signals": [_public_eval(s) for s in signals],
            "open_calls": [_public_call(c) for c in open_calls],
            "last_launch_at": (last or {}).get("last_launch"),
            "now": time.time(),
        }
    finally:
        conn.close()


@app.get("/signals")
def signals(limit: int = 50):
    conn = _conn()
    try:
        rows = db.fetchall_dicts(
            conn,
            """SELECT e.*, l.name, l.symbol FROM evaluations e
               JOIN raw_launches l ON l.mint = e.mint
               WHERE e.signal = 1 ORDER BY e.evaluated_at DESC LIMIT ?""",
            (max(1, min(limit, 200)),),
        )
        return {"signals": [_public_eval(r) for r in rows]}
    finally:
        conn.close()


@app.get("/track-record")
def track_record():
    conn = _conn()
    try:
        gate = ledger.gate_progress(conn)
        calls = db.fetchall_dicts(
            conn,
            """SELECT c.*, l.name, l.symbol FROM paper_calls c
               JOIN raw_launches l ON l.mint = c.mint
               ORDER BY c.signal_at DESC""",
        )
        return {
            "gate": gate,
            "calls": [_public_call(c) for c in calls],
            "honesty_note": (
                "Paper signals only. Every call is $10 hypothetical, graded "
                "at take-profit +100%, stop-loss -50%, or the 24-hour price, "
                "minus 1% fees per side. Paper results will look better than "
                "real results: real trading adds pool fees, priority fees, "
                "slippage, and failed transactions. Most memecoins lose money."
            ),
        }
    finally:
        conn.close()


def _public_eval(row):
    return {
        "mint": row["mint"],
        "name": row.get("name"),
        "symbol": row.get("symbol"),
        "evaluated_at": row["evaluated_at"],
        "age_seconds": row["age_seconds"],
        "score": row["score"],
        "breakdown": json.loads(row["breakdown_json"] or "[]"),
        "vetoes": json.loads(row["vetoes_json"] or "[]"),
        "unknowns": json.loads(row["unknowns_json"] or "[]"),
    }


def _public_call(row):
    return {
        "id": row["id"],
        "mint": row["mint"],
        "name": row.get("name"),
        "symbol": row.get("symbol"),
        "signal_at": row["signal_at"],
        "entry_price": row["entry_price"],
        "entry_source": row["entry_source"],
        "stake_usd": row["stake_usd"],
        "score": row["score"],
        "breakdown": json.loads(row["breakdown_json"] or "[]"),
        "status": row["status"],
        "exit_price": row["exit_price"],
        "exit_reason": row["exit_reason"],
        "exit_at": row["exit_at"],
        "net_return": row["net_return"],
    }
