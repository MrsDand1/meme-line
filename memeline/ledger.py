"""The Meme Line — paper ledger (design section 4).

$10 fixed per call, hypothetical. Exits: TP +100%, SL -50%, 24h time stop.
Fee 1% per side (2% round trip). Grade: net = (exit/entry - 1) - 0.02.
Win = net > 0. Grades are written once and never edited.
"""
import json
import time

from . import config
from .db import fetchall_dicts, fetchone_dict


class DailyCapReached(Exception):
    pass


def signals_today(conn, now=None):
    """How many signals were opened as paper calls today (UTC day)."""
    now = now if now is not None else time.time()
    day_start = now - (now % 86400)
    row = fetchone_dict(
        conn,
        "SELECT COUNT(*) AS n FROM paper_calls WHERE signal_at >= ?",
        (day_start,),
    )
    return row["n"] if row else 0


def open_call(conn, mint, signal_at, entry_price, entry_source, score, breakdown):
    """Open a paper call. Enforces the 5-per-day signal cap."""
    if signals_today(conn, signal_at) >= config.DAILY_SIGNAL_CAP:
        raise DailyCapReached(
            f"daily signal cap ({config.DAILY_SIGNAL_CAP}) reached"
        )
    cur = conn.execute(
        """INSERT INTO paper_calls
           (mint, signal_at, entry_price, entry_source, stake_usd, score,
            breakdown_json, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
        (mint, signal_at, entry_price, entry_source, config.STAKE_USD,
         score, json.dumps(breakdown)),
    )
    conn.commit()
    return cur.lastrowid


def net_return(entry_price, exit_price):
    """Net return after the 2% round-trip fee."""
    return (exit_price / entry_price - 1.0) - 2 * config.FEE_PER_SIDE


def grade_open_calls(conn, price_fn, now=None):
    """Grade every OPEN call whose exit has been hit.

    price_fn(mint, entry_source) -> (price_usd or None, source).
    First hit wins: TP, then SL, then the 24h time stop. A time stop with
    no price available stays open and is retried on the next tick —
    the exit price is never guessed.
    Returns a list of the calls closed on this tick.
    """
    now = now if now is not None else time.time()
    closed = []
    open_calls = fetchall_dicts(
        conn, "SELECT * FROM paper_calls WHERE status = 'OPEN' ORDER BY signal_at"
    )
    for call in open_calls:
        price, _source = price_fn(call["mint"], call["entry_source"])
        entry = call["entry_price"]
        exit_reason = None
        exit_price = None
        if price is not None:
            if price >= entry * config.TP_MULT:
                exit_reason, exit_price = "TP", price
            elif price <= entry * config.SL_MULT:
                exit_reason, exit_price = "SL", price
            elif now - call["signal_at"] >= config.TIME_STOP_HOURS * 3600:
                exit_reason, exit_price = "24h", price
        if exit_reason is None:
            continue  # still open (or no price yet for the time stop)
        net = net_return(entry, exit_price)
        conn.execute(
            """UPDATE paper_calls SET status='CLOSED', exit_price=?, exit_reason=?,
               exit_at=?, net_return=? WHERE id=?""",
            (exit_price, exit_reason, now, net, call["id"]),
        )
        conn.commit()
        closed.append({**call, "exit_price": exit_price,
                       "exit_reason": exit_reason, "exit_at": now,
                       "net_return": net})
    if closed:
        recompute_grades(conn)
    return closed


def recompute_grades(conn):
    """Recompute the running totals from paper_calls. Never hand-edited."""
    row = fetchone_dict(
        conn,
        """SELECT COUNT(*) AS calls,
                  COALESCE(SUM(CASE WHEN net_return > 0 THEN 1 ELSE 0 END), 0) AS wins,
                  COALESCE(SUM(net_return), 0.0) AS cumulative_net
           FROM paper_calls WHERE status = 'CLOSED'""",
    )
    calls = row["calls"]
    cumulative = row["cumulative_net"]
    avg = cumulative / calls if calls else 0.0
    conn.execute(
        """INSERT INTO grades (id, updated_at, calls, wins, cumulative_net, avg_net)
           VALUES (1, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,
               calls=excluded.calls, wins=excluded.wins,
               cumulative_net=excluded.cumulative_net, avg_net=excluded.avg_net""",
        (time.time(), calls, row["wins"], cumulative, avg),
    )
    conn.commit()
    return {"calls": calls, "wins": row["wins"],
            "cumulative_net": cumulative, "avg_net": avg}


def gate_progress(conn, now=None):
    """Gate status: 50 calls over at least 2 weeks, net positive after fees."""
    now = now if now is not None else time.time()
    totals = fetchone_dict(conn, "SELECT * FROM grades WHERE id = 1")
    if totals is None:
        totals = recompute_grades(conn)
    first = fetchone_dict(
        conn, "SELECT MIN(signal_at) AS first_at FROM paper_calls"
    )
    first_at = first["first_at"] if first else None
    days = (now - first_at) / 86400.0 if first_at else 0.0
    calls = totals["calls"]
    cumulative = totals["cumulative_net"]
    if calls >= config.GATE_CALLS and days >= config.GATE_MIN_DAYS:
        status = "PASSED" if cumulative > 0 else "FAILED"
    else:
        status = "IN_PROGRESS"
    return {
        "calls": calls,
        "gate_calls": config.GATE_CALLS,
        "wins": totals["wins"],
        "win_rate": (totals["wins"] / calls) if calls else 0.0,
        "cumulative_net": cumulative,
        "avg_net_per_call": totals["avg_net"],
        "days_elapsed": round(days, 1),
        "gate_min_days": config.GATE_MIN_DAYS,
        "status": status,
        "first_call_at": first_at,
    }
