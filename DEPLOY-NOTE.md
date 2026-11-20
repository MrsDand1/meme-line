# The Meme Line — Deploy Note: Solana direct feed (2026-11-20)

## Why

PumpPortal (`wss://pumpportal.fun/api/data`) has been unreachable from
Render, this VM, and the browser since 2026-11-20 ~07:06 ET. The
service was live and healthy but captured 0 launches. The launch feed
is now read straight from Solana instead of through PumpPortal.

## What changed

| File | Change |
|---|---|
| `memeline/solana_feed.py` | **NEW.** Direct launch feed: `logsSubscribe` on the pump.fun program (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) via the public RPC WebSocket `wss://api.mainnet-beta.solana.com` (free, no key). Client-side filter to `Create` / `CreateV2` log lines only; the launch is extracted from the **CreateEvent in the notification's own logs** (no RPC call), with one throttled `getTransaction` per candidate as a fallback when no event is in the logs; emits launch events in the exact PumpPortal shape, so capture, scoring, ledger, and the dashboard are unchanged. Same gap discipline as the PumpPortal client (every disconnect is a gap row). |
| `memeline/config.py` | Added `PUMP_PROGRAM_ID`, `SOLANA_WS_URL`, and `FEED_SOURCE` (env, default `"solana"`; set `"pumpportal"` to switch back). No new secrets, no new env vars required. |
| `memeline/capture.py` | `run_capture` now picks the feed via `select_feed_runner()` (Solana by default, PumpPortal on `FEED_SOURCE=pumpportal`). PumpPortal client code intact but inactive. |
| `memeline/pumpportal.py` | **Unchanged.** Fallback only. |
| `tests/test_solana_feed.py` | **NEW.** 23 tests: log filtering (incl. the `CreateAccount` / bare-`Create` traps), instruction-data decoding, extraction from the REAL Flyshake create transaction (fetched live from the public RPC), rejection cases (wrong discriminator, shifted layout, mint missing from balances), **logs-event extraction from the real Flyshake logs (CreateEvent decodes to the known values; the TradeEvent in the same transaction is skipped by discriminator; bad/truncated event lines are rejected)**, the feed loop against a fake WebSocket (**primary path proves `getTransaction` is never called; fallback path proves a no-event notification still emits via `getTransaction`; an unfetchable fallback candidate is dropped with a log line**), and the config switch. |
| `tests/test_wiring.py` | **NEW.** 3 tests — the two deploy lessons: (1) boot the REAL FastAPI app against a local SQLite DB and hit `/health`, proving the lifespan runs, tables are created, and capture starts; (2) same with capture disabled; (3) a source scan proving no `PRAGMA` statement exists in any runtime file (Turso's Hrana protocol rejects PRAGMA — that killed the 2026-11-20 redeploy). |
| `tests/fixtures/solana-create-tx.json` | **NEW.** The real Flyshake create transaction, fetched live from the public RPC 2026-11-20. Ground truth for the parser. |
| `README.md`, `DEPLOY.md` | Feed description updated from PumpPortal to the direct Solana feed. |

## Key facts the build rests on (all verified live, 2026-11-20)

- Current launches use the **CreateV2** instruction, which logs
  `Program log: Instruction: CreateV2`. Legacy Create logs
  `Instruction: Create`. Both are accepted.
- A create transaction ALSO carries a **BuyV2** instruction (the
  creator's initial buy). The parser picks the create instruction by
  discriminator, never just "a pump instruction".
- CreateV2 discriminator `d6904cec5f8b31b4`; legacy Create
  `181e741cc9ba6f46`. Instruction data is borsh: discriminator, then
  name, symbol, uri as length-prefixed strings.
- CreateV2 accounts: mint, mint_authority, bonding_curve,
  associated_bonding_curve, global, user (creator), ... The creator
  is the fee payer — that cross-check is used instead of a fixed
  creator index (Create and CreateV2 disagree on it). The mint must
  appear in the transaction's token balances or the candidate is
  dropped.
- Create transactions are versioned (v0) with address lookup tables;
  account indices resolve against static keys + loaded writable +
  loaded readonly.
- **The CreateEvent is in the logs.** The pump program emits a
  `CreateEvent` as a `Program data: <base64>` log line on every
  create: borsh discriminator `1b72a94ddeeb6376`
  (= sha256("event:CreateEvent")[:8]), then name, symbol, uri, then
  mint, bonding_curve, user (creator) as 32-byte pubkeys. Verified
  against the Flyshake fixture — every field decodes to the known
  values. The same transaction also logs a `TradeEvent` (its BuyV2)
  with a different discriminator, so the extractor matches the
  CreateEvent discriminator exactly. The event carries no associated
  bonding-curve key (same as PumpPortal); capture resolves the curve
  account via getTokenAccountsByOwner at evaluation time.
- **Bonus over PumpPortal (fallback path only):** when the launch is
  enriched via `getTransaction`, the Solana transaction carries the
  associated bonding-curve key directly (PumpPortal never did).
- **Not in this feed:** migration (graduation) events. Graduation is
  still detected at evaluation time via DexScreener, which is what
  scoring uses; nothing depends on the migration event.

## How to deploy

1. Commit these files to `main` in `MrsDand1/meme-line`.
2. Render dashboard → `meme-line-api` → **Manual Deploy** (Auto-Deploy
   stays **Off**). No new environment variables are needed:
   `FEED_SOURCE` defaults to `solana`. (`HELIUS_API_KEY`,
   `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `CAPTURE_ENABLED=1`
   are already set.)
3. Post-deploy checks, in order:
   - `GET /health` → `status: ok`, `capture_running: true`.
   - `GET /health` again after 2–3 minutes → `last_launch_at` is no
     longer `null` (launches arrive ~30–40/min at busy times).
   - Wait 10 minutes: the first evaluation appears in
     `/track-record` (or in `evaluations` with status SKIPPED if the
     token failed the pre-filter).
   - `GET /live` → gate progress shows `IN_PROGRESS`.
   - Render logs should show `[solana-feed] connected, subscribed to
     pump.fun logs`. `dropped candidate` lines should be rare now
     (they only come from the getTransaction fallback, when a
     notification carries no CreateEvent); a steady stream of them
     means the public RPC is throttling the fallback or the event
     format changed — check the logs, not just `last_launch_at`.

## Rollback

- To switch back to PumpPortal (if it recovers): set the Render env
  var `FEED_SOURCE=pumpportal` and Manual Deploy. No code change.
- To stop capture entirely: set `CAPTURE_ENABLED=0` (or suspend the
  Render service). The database keeps every launch, evaluation, and
  graded call — nothing is deleted.

## Verification done in this build

- Full pytest suite: **75 passed** (49 pre-existing + 26 new), 0 failed.
  (One pre-existing Starlette deprecation warning about httpx — unrelated.)
- No `PRAGMA` in any runtime file (grep check, see test_wiring.py).
- No secrets in any file (grep for key/token/password — only the
  documented env-var names).
- Live smoke test (this VM, public RPC): see below.

### Live smoke test

**First smoke run — getTransaction-only build (2026-11-20 ~08:04 ET,
70 s): the feed worked, but enrichment failed.** 10,947 log
notifications arrived (~156/s), 28 were real creates (~24/min), and
only **5 were enriched — 23 of 28 failed** (`getTransaction` returned
null or errored). An 82% drop rate would have meant missing most
launches in production, so the feed was **not deployed as-is**.

**Diagnosis (2026-11-20 ~08:05–08:20 ET):** not replication lag and
not parsing. A diagnostic fetched one normal 88-char signature at
t+0, 1, 3, 5, 10, **30 s** and got **200-null every time**; a raw
notification capture showed 5/5 create notifications carry real
signatures with genuine CreateV2 logs. Conclusion: the public RPC
pool, as reached from this environment, will not serve most fresh
transactions (old transactions fetch fine; roughly 1 in 5 fresh ones
do). That finding drove the redesign: extract the launch from the
logs themselves instead of depending on `getTransaction`.

**Second smoke run — logs-event build (2026-11-20 ~08:20 ET, 70 s):
the redesign works end-to-end.** **26 launches, ALL 26 via the
logs-event path, 0 `getTransaction` calls, 0 dropped candidates,
0 gaps, 0 disconnects** (one reconnect = the initial connect). A
repeat run immediately before it showed the same pattern (5 launches,
all logs_event). Sample launches carried real mints/names/symbols
(e.g. "Doomed Rocket" / DOOROC, "PIZZA", "bullThropic" / ANTBULL).
The `getTransaction` fallback is untested live against fresh
transactions in this environment (it is only reached when a
notification carries no CreateEvent, which never happened in ~150 s
of live traffic) — it is unit-tested with the real Flyshake
transaction, and on Render the service connects directly, where the
public RPC may serve fresh transactions better than through this
sandbox's proxy.

## Open risks (honest)

- **Public RPC getTransaction is unreliable for fresh transactions
  (measured, 2026-11-20).** The fallback path depends on it, and from
  this environment it returned 200-null for a fresh transaction even
  30 s after confirmation. The primary logs-event path does not
  depend on it at all (26/26 launches in the live smoke test needed
  zero RPC calls), so this only matters on the rare no-event fallback.
  If the fallback ever matters at volume, the fix is to enrich via
  the Helius RPC already in the Render env (1 credit per
  getTransaction, ~50k/month at 40 creates/min — inside the free
  1M credits/mo), not a new service. That switch is a small code
  change, not a deploy blocker.
- **CreateEvent-format drift.** If pump.fun stops emitting the
  CreateEvent or changes its layout, the primary path fails closed
  (candidates fall to the getTransaction fallback, which is the
  unreliable path above) — launches would thin out until the parser
  is updated. Watch `last_launch_at` and the `dropped candidate`
  log lines after deploy; the 24h soak is the real check.
- **Public RPC throttling / WS stability.** The public RPC is
  documented "not for production" (100 req/10 s per IP, 40 req/10 s
  per method). The feed now makes no RPC calls on the primary path
  (one WS subscription only), but Render's outbound IP is shared, so
  a burst or a noisy neighbour can still drop the subscription. The
  feed reconnects with backoff and logs a gap — the gap log is the
  record, not a silent miss. The first paid step if that ever bites
  is Helius Developer ($49/mo) — her call, not a default.
- **Firehose volume.** `logsSubscribe` delivers EVERY pump.fun
  transaction (buys/sells included, thousands per minute); the filter
  is client-side. If the public RPC drops the subscription under load,
  the feed reconnects with backoff and logs a gap — the gap log is
  the record, not a silent miss.
- **Instruction-layout drift.** If pump.fun changes the CreateV2
  account order or discriminator, extraction fails closed (candidates
  dropped, never misread) — but launches would stop until the parser
  is updated. The mint-in-balances cross-check is the tripwire.
- **24-hour feed soak still open.** The design's step-2 check: prove
  the feed stays up for 24 hours on the deployed service, with the
  gap log reviewed. Cannot be done in one build session.
- **No migration events from this feed** (see above) — graduation
  detection via DexScreener is unchanged, so scoring is unaffected.
