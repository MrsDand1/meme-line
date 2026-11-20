# The Meme Line — Stage 1 (paper-first signals bot)

Signals only, paper only, $0/month. A watcher, not a trader: it listens
to new pump.fun launches, scores the ones that survive 10 minutes on a
fixed checklist, and posts a signal when one passes. Members keep custody
and place their own trades. No auto-trading, no wallet, no private key,
no SOL anywhere in this codebase.

Design: [stage0-design.md](stage0-design.md) — approved 2026-11-19.
Marketing plan: the product is The Meme Line, audience careful beginners,
home THE FAIR LINE Discord third wing, $25/month all-in, paid access only
after the validation gate passes (50 paper calls over ≥ 2 weeks, net
positive after fees).

## What it is

- **Feed:** PumpPortal WebSocket — new-token launches + migration
  (graduation) events, one connection, reconnect with backoff, gap logging.
- **Evaluation:** 60-second poller, exactly once per token at age 10
  minutes (grace to 15 min; older than that is SKIPPED, never backfilled).
- **Score:** fixed 0–10 rule (see `memeline/scoring.py`), signal at ≥ 7.
  A failed check is "unknown" and scores 0 — never a pass.
- **Paper ledger:** $10/call, TP +100% / SL −50% / 24h time stop, 1% fee
  per side. Grades written once, never edited.
- **Public API + dashboard:** `/live`, `/signals`, `/track-record`,
  `/health`, static dashboard at `/`.

## Layout

| File | What it does |
|---|---|
| `capture.py` | Feed + poller loop (the worker) |
| `tracker.py` | FastAPI public API + dashboard (the web service) |
| `memeline/config.py` | Frozen v1 constants (approved rule — do not change) |
| `memeline/db.py` | libsql: Turso remote or local file |
| `memeline/providers.py` | DexScreener, RugCheck, Jupiter, Helius/public RPC |
| `memeline/scoring.py` | Frozen v1 scoring rule (pure functions) |
| `memeline/ledger.py` | Paper calls, grading, gate progress |
| `memeline/discord.py` | Discord webhook signal posts |
| `memeline/pumpportal.py` | PumpPortal WebSocket client |
| `schema.sql` | Tables: raw_launches, evaluations, paper_calls, grades, gaps, meta |
| `static/index.html` | Public track-record dashboard |
| `render.yaml` | Render free web service |
| `tests/` | pytest suite (offline fixtures + live smoke checks) |

## Price sources (frozen for the run — the source must not change mid-run)

- Graduated token: DexScreener best-pair price (`dexscreener`).
- Still on the curve: Jupiter lite-api Price v3 (`jupiter`).
- If the frozen source has no price, the call is not opened / not graded
  yet — the price is never guessed.

## Curve progress

Bonding-curve progress = 1 − (curve token balance / total supply).
The PumpPortal event carries the curve PDA but not its token account,
so the account is resolved at evaluation time with
`getTokenAccountsByOwner` and its balance read via RPC. The curve
starts holding the token's full supply — verified live 2026-11-20 on
a real launch (curve balance 966,752,011 + 33,247,989 held outside =
1,000,000,000 = total supply). An older figure of 793.1M circulates
for the original curve design; it is wrong for current launches and
is not used.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q        # unit tests (offline fixtures)
DATABASE_PATH=memeline.db .venv/bin/python capture.py   # feed + poller
DATABASE_PATH=memeline.db .venv/bin/uvicorn tracker:app --port 8000
```

With no `HELIUS_API_KEY` set, on-chain reads fall back to the public
Solana RPC. With no `DISCORD_WEBHOOK_URL` set, signals are logged to
the database and not posted anywhere.

## Environment variables (deploy)

| Variable | Required | Who holds it |
|---|---|---|
| `TURSO_DATABASE_URL` | yes (prod) | Turso dashboard |
| `TURSO_AUTH_TOKEN` | yes (prod) | Turso dashboard |
| `HELIUS_API_KEY` | yes (prod) | **Cheli — never in chat, never in a file** |
| `DISCORD_WEBHOOK_URL` | no | Discord server settings |
| `CAPTURE_ENABLED` | no | `1` to run the feed inside the web service |
| `DATABASE_PATH` | no | local dev only (default `memeline.db`) |

See [DEPLOY.md](DEPLOY.md) for the deploy steps and the checks that
are still open (24h feed soak, Helius DAS authority check, DexScreener
terms read).
