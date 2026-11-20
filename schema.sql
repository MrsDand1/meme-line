-- The Meme Line — data schema (Stage 1, 2026-11-19)
-- SQLite / Turso (libsql). Every launch is stored raw with its receipt
-- timestamp; nothing is ever overwritten or deleted. Paper calls are
-- graded once and never edited. Signals only, paper only, no money.
-- Mirrors the BTC Oracle Pro schema habits (raw log + ledger + grades).

CREATE TABLE IF NOT EXISTS raw_launches (
    mint                    TEXT PRIMARY KEY,
    name                    TEXT,
    symbol                  TEXT,
    creator                 TEXT,
    bonding_curve_key       TEXT,
    associated_curve_key    TEXT,
    launched_at             REAL NOT NULL,  -- unix seconds, when WE received the event
    graduated_at            REAL,           -- set by a migration event, NULL until then
    pool_address            TEXT,           -- AMM pool address after graduation, if known
    raw_json                TEXT NOT NULL   -- the PumpPortal event, verbatim
);

CREATE TABLE IF NOT EXISTS evaluations (
    mint            TEXT PRIMARY KEY,
    evaluated_at    REAL NOT NULL,  -- unix seconds
    age_seconds     REAL NOT NULL,  -- age of the token when evaluated
    score           INTEGER,        -- NULL when status is SKIPPED or ERROR
    signal          INTEGER NOT NULL DEFAULT 0,  -- 1 = score >= 7 and no vetoes
    status          TEXT NOT NULL DEFAULT 'EVALUATED',  -- EVALUATED | SKIPPED | ERROR
    breakdown_json  TEXT,           -- per-check points + details
    vetoes_json     TEXT,           -- vetoes that fired (empty list when none)
    unknowns_json   TEXT,           -- checks that could not be verified (never a pass)
    note            TEXT
);

CREATE TABLE IF NOT EXISTS paper_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mint            TEXT NOT NULL,
    signal_at       REAL NOT NULL,  -- unix seconds
    entry_price     REAL NOT NULL,  -- price in USD at signal time
    entry_source    TEXT NOT NULL,  -- 'dexscreener' | 'jupiter'
    stake_usd       REAL NOT NULL,  -- $10.00 fixed, hypothetical
    score           INTEGER NOT NULL,
    breakdown_json  TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED
    exit_price      REAL,           -- NULL until closed
    exit_reason     TEXT,           -- 'TP' | 'SL' | '24h' | NULL until closed
    exit_at         REAL,           -- NULL until closed
    net_return      REAL            -- (exit/entry - 1) - 0.02, NULL until closed
);

CREATE TABLE IF NOT EXISTS grades (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at      REAL NOT NULL,
    calls           INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    cumulative_net  REAL NOT NULL DEFAULT 0.0,  -- sum of net_return, after fees
    avg_net         REAL NOT NULL DEFAULT 0.0   -- cumulative_net / calls
);

CREATE TABLE IF NOT EXISTS gaps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,  -- unix seconds
    ended_at    REAL,           -- NULL while the gap is still open
    reason      TEXT NOT NULL   -- 'ws_disconnect' | 'poller_silence' | 'service_sleep'
);

CREATE TABLE IF NOT EXISTS meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_launches_time
    ON raw_launches (launched_at);
CREATE INDEX IF NOT EXISTS idx_calls_status_time
    ON paper_calls (status, signal_at);
CREATE INDEX IF NOT EXISTS idx_evals_signal
    ON evaluations (signal);
