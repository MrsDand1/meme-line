"""The Meme Line — capture loop (design section 5, build steps 2-5).

One WebSocket feed (PumpPortal, one connection) logs every launch.
A 60-second poller evaluates tokens at age 10 minutes, opens paper
calls on signals, and grades open calls. Missed evaluation windows are
SKIPPED and logged, never backfilled and never guessed. A failed check
is "unknown", never a pass.
"""
import asyncio
import json
import signal
import time

from . import config, db, discord, ledger, providers, scoring
from .pumpportal import run_feed


# ---------------------------------------------------------------------------
# Launch + migration intake
# ---------------------------------------------------------------------------

def store_launch(conn, event):
    """Store a raw launch. Prefilter failures are stored too — the
    firehose log is never filtered — but flagged by the evaluation step."""
    conn.execute(
        """INSERT OR IGNORE INTO raw_launches
           (mint, name, symbol, creator, bonding_curve_key,
            associated_curve_key, launched_at, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event["mint"], event["name"], event["symbol"], event["creator"],
         event["bonding_curve_key"], event["associated_curve_key"],
         time.time(), json.dumps(event["raw"])),
    )
    conn.commit()


def store_migration(conn, event):
    """Record a graduation (migration) event on its launch row."""
    conn.execute(
        """UPDATE raw_launches SET graduated_at = ?, pool_address = ?
           WHERE mint = ?""",
        (time.time(), event["pool_address"], event["mint"]),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Evidence gathering (one provider failure = unknown for that check)
# ---------------------------------------------------------------------------

def _fetch_metadata_socials(uri):
    """Fetch a token metadata JSON (uri) and look for social links.

    Returns (found, errored): found=True when an X/Telegram/website link
    is present; errored=True when the fetch itself failed.
    """
    if not uri:
        return False, False
    try:
        import requests
        r = requests.get(uri, timeout=config.HTTP_TIMEOUT_SECONDS)
        if r.status_code != 200:
            return False, True
        meta = r.json()
    except Exception:  # noqa: BLE001 - any fetch/parse failure is "unknown"
        return False, True
    if not isinstance(meta, dict):
        return False, False
    for key in ("twitter", "telegram", "website", "x"):
        if meta.get(key):
            return True, False
    ext = meta.get("extensions") or {}
    for key in ("twitter", "telegram", "website"):
        if ext.get(key):
            return True, False
    return False, False


def gather_evidence(conn, launch):
    """Build the evidence dict for scoring. Never raises for a provider
    failure — that check is recorded as unknown instead."""
    mint = launch["mint"]
    evidence = {
        "dev_pct": None,
        "top10_pct_ex": None,
        "rugcheck_score": None,
        "rugcheck_danger": False,
        "graduated": False,
        "liquidity_usd": None,
        "curve_progress_pct": None,
        "has_socials": False,
        "creator_clean": None,
        "freeze_authority": None,
        "creator": launch.get("creator") or None,
        "has_market": False,
    }

    # --- RugCheck (throttled inside the client) --------------------------
    # The curve token account (for the top-10 exclusion) is resolved
    # first: the event carries the curve PDA, not its token account.
    curve_account = None
    try:
        curve_account = providers.curve_token_account(
            launch.get("bonding_curve_key"), mint,
            config.HELIUS_API_KEY or None,
        )
    except providers.ProviderError:
        curve_account = None
    try:
        report = providers.rugcheck_report(mint)
        evidence["dev_pct"] = providers.rugcheck_dev_pct(report)
        evidence["rugcheck_score"] = report.get("score_normalised")
        evidence["rugcheck_danger"] = any(
            (r or {}).get("level") == "danger" for r in (report.get("risks") or [])
        )
        evidence["creator_clean"] = providers.rugcheck_creator_clean(report, mint)
        mint_auth, freeze_auth = providers.rugcheck_authorities(report)
        evidence["freeze_authority"] = freeze_auth
        evidence["_mint_authority"] = mint_auth
    except providers.ProviderError:
        report = None

    # --- DexScreener: graduation, liquidity, socials ----------------------
    pair = None
    try:
        pair = providers.dexscreener_best_pair(mint)
    except providers.ProviderError:
        pair = None
    if pair is not None:
        evidence["graduated"] = pair["dex_id"] in config.GRADUATION_DEX_IDS or (
            pair["liquidity_usd"] is not None
            and pair["liquidity_usd"] >= config.GRAD_MIN_LIQUIDITY_USD
        )
        evidence["liquidity_usd"] = pair["liquidity_usd"]
        evidence["has_market"] = True
        if pair["has_socials"]:
            evidence["has_socials"] = True

    # --- Top-10 holders, ex-curve/pool -------------------------------------
    # RugCheck's topHolders INCLUDE the bonding-curve account (it is
    # usually the #1 holder at ~97%), so the curve account must be
    # excluded for the number to mean anything. When the token is still
    # on the curve and the curve account could not be resolved, the
    # top-10 figure is contaminated and is recorded as unknown — never
    # as a pass, and never as a veto on a number we know is wrong.
    # (Live evidence 2026-11-20: unresolved curve account -> top-10
    # read 100.00% and vetoed a token on the curve's own holdings.)
    if report is not None:
        if evidence["graduated"] or curve_account:
            evidence["top10_pct_ex"] = providers.rugcheck_top10_ex(
                report, [curve_account] if curve_account else []
            )
        else:
            evidence["top10_pct_ex"] = None

    # --- Metadata socials (only when DexScreener found none) --------------
    if not evidence["has_socials"]:
        uri = launch.get("uri") or ""
        found, _errored = _fetch_metadata_socials(uri)
        if found:
            evidence["has_socials"] = True

    # --- Curve progress (only while still on the curve) --------------------
    if not evidence["graduated"]:
        try:
            supply = None
            if report is not None:
                token_info = report.get("token") or {}
                raw_supply = token_info.get("supply")
                decimals = token_info.get("decimals", 6)
                if isinstance(raw_supply, (int, float)):
                    supply = raw_supply / (10 ** decimals)
            evidence["curve_progress_pct"] = providers.curve_progress_pct(
                launch.get("bonding_curve_key"), mint,
                config.HELIUS_API_KEY or None,
                curve_account=curve_account,
                total_supply=supply,
            )
            if evidence["curve_progress_pct"] is not None:
                evidence["has_market"] = True
        except providers.ProviderError:
            pass

    # --- Authorities cross-check (Helius first, public RPC fallback) -------
    # RugCheck already gives authorities; only re-check on-chain when it
    # did not (or when its freeze authority is present and must be
    # attributed). The freeze veto compares against the creator address.
    if evidence["freeze_authority"] is None:
        try:
            _ma, fa, _src = providers.mint_authorities(
                mint, config.HELIUS_API_KEY or None
            )
            evidence["freeze_authority"] = fa
            evidence.setdefault("_mint_authority", _ma)
        except providers.ProviderError:
            pass

    # --- Zero-liquidity veto input ------------------------------------------
    if not evidence["has_market"]:
        try:
            quote = providers.jupiter_price(mint)
            if quote:
                evidence["has_market"] = True
        except providers.ProviderError:
            pass
    return evidence


# ---------------------------------------------------------------------------
# Evaluation tick
# ---------------------------------------------------------------------------

def evaluate_launch(conn, launch, now=None):
    """Evaluate one launch. Returns the evaluation result dict, or None
    when the launch is not due / already handled. Writes the evaluation
    row, opens a paper call on a signal, and posts to Discord."""
    now = now if now is not None else time.time()
    mint = launch["mint"]
    age = now - launch["launched_at"]
    existing = db.fetchone_dict(
        conn, "SELECT mint FROM evaluations WHERE mint = ?", (mint,)
    )
    if existing:
        return None

    ok, reason = scoring.prefilter(launch)
    if not ok:
        conn.execute(
            """INSERT INTO evaluations (mint, evaluated_at, age_seconds, score,
               signal, status, note) VALUES (?, ?, ?, NULL, 0, 'SKIPPED', ?)""",
            (mint, now, age, f"prefilter failed: {reason}"),
        )
        conn.commit()
        return {"mint": mint, "status": "SKIPPED",
                "note": f"prefilter failed: {reason}"}

    if age < config.EVAL_AGE_SECONDS:
        return None  # not due yet
    if age > config.EVAL_AGE_SECONDS + config.EVAL_GRACE_SECONDS:
        conn.execute(
            """INSERT INTO evaluations (mint, evaluated_at, age_seconds, score,
               signal, status, note) VALUES (?, ?, ?, NULL, 0, 'SKIPPED', ?)""",
            (mint, now, age, "missed evaluation window (older than 15 min)"),
        )
        conn.commit()
        return {"mint": mint, "status": "SKIPPED",
                "note": "missed evaluation window"}

    try:
        evidence = gather_evidence(conn, launch)
        result = scoring.score(evidence)
    except Exception as e:  # noqa: BLE001 - evaluation must never crash the tick
        conn.execute(
            """INSERT INTO evaluations (mint, evaluated_at, age_seconds, score,
               signal, status, note) VALUES (?, ?, ?, NULL, 0, 'ERROR', ?)""",
            (mint, now, age, f"evaluation error: {type(e).__name__}: {e}"),
        )
        conn.commit()
        return {"mint": mint, "status": "ERROR", "note": str(e)}

    conn.execute(
        """INSERT INTO evaluations (mint, evaluated_at, age_seconds, score,
           signal, status, breakdown_json, vetoes_json, unknowns_json)
           VALUES (?, ?, ?, ?, ?, 'EVALUATED', ?, ?, ?)""",
        (mint, now, age, result["score"], 1 if result["signal"] else 0,
         json.dumps(result["checks"]), json.dumps(result["vetoes"]),
         json.dumps(result["unknowns"])),
    )
    conn.commit()

    if not result["signal"]:
        return {"mint": mint, "status": "EVALUATED", **result}

    # Signal: open a paper call at the frozen entry-price source.
    entry_price, entry_source = (None, None)
    try:
        entry_price, entry_source = providers.entry_price(
            mint, evidence["graduated"]
        )
    except providers.ProviderError:
        pass
    if entry_price is None:
        conn.execute(
            "UPDATE evaluations SET note = ? WHERE mint = ?",
            ("signal but no entry price — call not opened", mint),
        )
        conn.commit()
        return {"mint": mint, "status": "EVALUATED", "note": "no entry price",
                **result}

    try:
        call_id = ledger.open_call(
            conn, mint, now, entry_price, entry_source,
            result["score"], result["checks"],
        )
    except ledger.DailyCapReached:
        conn.execute(
            "UPDATE evaluations SET note = ? WHERE mint = ?",
            ("signal but daily cap reached — call not opened", mint),
        )
        conn.commit()
        return {"mint": mint, "status": "EVALUATED", "note": "daily cap",
                **result}

    call = {"id": call_id, "mint": mint, "name": launch.get("name"),
            "symbol": launch.get("symbol"), "entry_price": entry_price,
            "entry_source": entry_source}
    discord.post_signal(config.DISCORD_WEBHOOK_URL, call, result)
    return {"mint": mint, "status": "EVALUATED", "call_id": call_id, **result}


def evaluate_due(conn, now=None):
    """Evaluate every launch due for its 10-minute check on this tick."""
    now = now if now is not None else time.time()
    due = db.fetchall_dicts(
        conn,
        """SELECT l.* FROM raw_launches l
           LEFT JOIN evaluations e ON e.mint = l.mint
           WHERE e.mint IS NULL
             AND l.launched_at <= ?
             AND l.launched_at > ?
           ORDER BY l.launched_at""",
        (now - config.EVAL_AGE_SECONDS,
         now - config.EVAL_AGE_SECONDS - config.EVAL_GRACE_SECONDS),
    )
    results = []
    for launch in due:
        results.append(evaluate_launch(conn, launch, now))
    # Anything still unevaluated and older than the grace window is skipped.
    stale = db.fetchall_dicts(
        conn,
        """SELECT l.* FROM raw_launches l
           LEFT JOIN evaluations e ON e.mint = l.mint
           WHERE e.mint IS NULL
             AND l.launched_at <= ?
           ORDER BY l.launched_at""",
        (now - config.EVAL_AGE_SECONDS - config.EVAL_GRACE_SECONDS,),
    )
    for launch in stale:
        results.append(evaluate_launch(conn, launch, now))
    return [r for r in results if r is not None]


def grade_tick(conn, now=None):
    """Grade open calls on this tick. Returns the calls closed."""
    return ledger.grade_open_calls(
        conn,
        lambda mint, src: providers.current_price(mint, src),
        now=now,
    )


# ---------------------------------------------------------------------------
# Gap logging
# ---------------------------------------------------------------------------

def open_gap(conn, started_at, reason):
    conn.execute(
        "INSERT INTO gaps (started_at, ended_at, reason) VALUES (?, NULL, ?)",
        (started_at, reason),
    )
    conn.commit()


def close_open_gaps(conn, ended_at):
    conn.execute(
        "UPDATE gaps SET ended_at = ? WHERE ended_at IS NULL", (ended_at,)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Poller + feed tasks
# ---------------------------------------------------------------------------

async def poller_loop(conn, stop_event):
    """60-second tick: evaluate due launches, grade open calls."""
    last_tick = None
    while not stop_event.is_set():
        now = time.time()
        if last_tick is not None and now - last_tick > 150:
            open_gap(conn, last_tick, "poller_silence")
            close_open_gaps(conn, now)
        try:
            await asyncio.to_thread(evaluate_due, conn, now)
            await asyncio.to_thread(grade_tick, conn, now)
        except Exception as e:  # noqa: BLE001 - a failed tick never kills the loop
            open_gap(conn, now, f"poller_error: {type(e).__name__}")
        last_tick = now
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=config.POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


def make_feed_callbacks(conn):
    def on_launch(event):
        store_launch(conn, event)

    def on_migration(event):
        store_migration(conn, event)

    def on_gap(started_at, reason):
        open_gap(conn, started_at, reason)

    def on_reconnect():
        close_open_gaps(conn, time.time())

    return on_launch, on_migration, on_gap, on_reconnect


async def run_capture(database_path=None):
    """Run the feed + poller until stopped (SIGTERM/SIGINT)."""
    feed_conn = db.connect(database_path=database_path)
    poll_conn = db.connect(database_path=database_path)
    db.init_db(feed_conn)
    db.init_db(poll_conn)
    stop_event = asyncio.Event()

    def _stop(*_args):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            pass

    on_launch, on_migration, on_gap, on_reconnect = make_feed_callbacks(feed_conn)
    await asyncio.gather(
        run_feed(on_launch, on_migration, on_gap, on_reconnect, stop_event),
        poller_loop(poll_conn, stop_event),
    )


def main():
    asyncio.run(run_capture())


if __name__ == "__main__":
    main()
