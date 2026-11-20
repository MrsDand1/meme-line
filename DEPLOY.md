# The Meme Line — Deploy Package (Stage 1)

Deploy-ready, not yet deployed. Do not deploy without her word.

## What is in this package

Working code in `~/workspace/meme-line/` with tests (see README.md for
the layout). Verified by the Stage 1 test suite plus a live smoke check
against the free public feeds (PumpPortal, DexScreener, Jupiter,
RugCheck, Solana public RPC). See "Verification" below.

## Environment variables (set in the Render dashboard, never in files)

| Variable | Value / source | Required |
|---|---|---|
| `TURSO_DATABASE_URL` | From the Turso dashboard (create a free database) | Yes |
| `TURSO_AUTH_TOKEN` | From the Turso dashboard | Yes |
| `HELIUS_API_KEY` | **Cheli holds it** — created 2026-11-19 on her Helius account. Paste it into Render only; never into chat, never into a file. | Yes |
| `DISCORD_WEBHOOK_URL` | Create in THE FAIR LINE Discord (third wing channel → Integrations → Webhooks). Optional — without it, signals are only in the database/dashboard. | No |
| `CAPTURE_ENABLED` | `1` (already in render.yaml) — runs the feed + poller inside the web service, so the $0 plan needs only one service. | No |

No wallet, no SOL, no private key anywhere. The only key in the system
is the Helius *read* key: revocable, holds no funds.

## Deploy steps

1. Create a Turso database (free tier) → copy URL + auth token.
2. Create a GitHub repo for this directory (same pattern as
   `MrsDand1/btc-oracle-pro-api`), push these files.
3. Render → New Web Service → connect the repo → `render.yaml` is
   picked up automatically. Set the four env vars above in the
   dashboard. Deploy.
4. Post-deploy checks (in order):
   - `GET /health` → `status: ok`, `capture_running: true`.
   - Watch the logs for `[solana-feed] connected, subscribed to
     pump.fun logs`. `GET /health` again after 2–3 minutes →
     `last_launch_at` is no longer `null` (launches arrive
     ~30–40/min at busy times).
   - Wait 10 minutes: the first evaluation should appear in
     `/track-record` (or in `evaluations` with status SKIPPED if the
     token failed the pre-filter).
   - `GET /live` → gate progress shows `IN_PROGRESS`.

   Rollback to PumpPortal (if it recovers): set the Render env var
   `FEED_SOURCE=pumpportal` and Manual Deploy. See DEPLOY-NOTE.md
   for the Solana-feed change (2026-11-20).

## Verification done at Stage 1 (2026-11-19/20)

- pytest suite green (see `tests/`): scoring (incl. the design's
  MOONCAT worked example → 9, SIGNAL), ledger math (TP +98%, SL −52%,
  flat −2%), provider parsing against live-fetched fixtures, DB
  round-trips, pre-filter and evaluation-window rules.
- Live smoke check: DexScreener, RugCheck, Jupiter lite-api and the
  Solana public RPC all returned 200 with the expected fields for a
  real token. Mint-authority decoding verified on a live mint account
  (both authorities revoked on the sampled token). PumpPortal HTTPS
  reachable (200); the WebSocket handshake timed out once from this VM
  (direct egress is proxied here — the client reads the proxy from the
  environment for local testing; Render connects directly).
- Curve-progress constant: the smoke check PROVED the circulating
  793.1M figure wrong — a live curve held 966,752,011 tokens, more
  than 793.1M. Verified instead: the curve starts holding the token's
  full total supply (curve balance + outside holdings = 1,000,000,000
  = supply, to the token). Progress is now 1 − balance/total_supply.
  The code was corrected (providers.py, capture.py) and the tests
  updated; see README.md.

## Still open (do not skip before launch)

1. **24-hour feed soak** — the design's step-2 check: prove the
   direct Solana feed stays up for 24 hours on the deployed service,
   with the gap log reviewed. Cannot be done in one build session.
   (PumpPortal, the original feed, has been unreachable since
   2026-11-20; the direct feed replaces it — see DEPLOY-NOTE.md.)
2. **Helius DAS authority check on a live pump.fun mint** — verify
   `token_info.mint_authority` / `freeze_authority` via Helius on a
   real pump.fun token (the public-RPC decode was verified instead;
   Helius needs the key, which only Cheli holds).
3. **DexScreener terms read** — the design flags a terms question
   (resale/redistribution, competing products). Read the current terms
   before the track-record page goes public.
4. **Terms review** before the first paying member (same rule as BTC).
5. **The 8 open questions** in the Stage 0 design, section 7 — the
   build uses the proposed defaults (5/day cap, $10 stake, 10-minute
   window, auto-post webhook, kill-on-gate-fail). Any change is a
   decision for Cheli, not a code change made silently.

## Rollback

The service is stateless apart from Turso. To stop it: set
`CAPTURE_ENABLED=0` (or suspend the Render service). The database
keeps every launch, evaluation, and graded call — nothing is deleted.
