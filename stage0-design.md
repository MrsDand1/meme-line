# The Meme Line — Stage 0 Design (paper-first signals bot)

**Status:** Design only. No build, no accounts created, no money spent, no wallet, no key handled.
**Date:** November 19, 2026. **She approves this design before any build starts. Do not start Stage 1.**

## Decisions already locked (do not re-decide)

- **Name:** The Meme Line. **Audience:** careful beginners learning memecoins without getting wrecked.
- **Home:** THE FAIR LINE Discord, third wing (next to BTC and picks).
- **Signals only.** The bot scores and alerts; members keep custody and place their own trades.
  Auto-trading for customers is rejected. No custody, no per-trade fees from us.
- **Price:** $25/month all-in (decided). Paid access opens ONLY after validation.
- **Validation gate:** 50 paper calls over at least 2 weeks, net positive after fees, every call posted publicly.
- **Style:** plain English, no locks/touts/profit promises, every call tracked wins and losses, 18+.

---

## 1. What it is

A watcher, not a trader. It listens to every new token launch on Solana's pump.fun,
waits, scores the ones that survive the first minutes, and posts a signal when one
passes a fixed checklist. A member reads the signal, checks it herself, and decides
whether to buy with her own wallet. The bot never touches money, never holds a key,
never places a trade.

The honest positioning, carried over from the marketing plan: this product does **not**
compete on speed. The established bots buy in the first seconds. The Meme Line
deliberately signals later — at 10 minutes old — and its edge is selection and
transparency, not being first.

---

## 2. Data sources — all verified by live check, November 19, 2026

Every figure below was checked today against the provider's own docs or a current
comparison that cites them. Limits and prices change; Stage 1 re-checks each one
before relying on it.

| Source | What it gives us | Cost | Key? | Rate limit (checked) | Role in v1 |
|---|---|---|---|---|---|
| **PumpPortal WS** `wss://pumpportal.fun/api/data` | Live new-token launches + graduation (migration) events, real time | $0 | No | No published number; ~30–40 launches/min observed. **One connection only — multiple connections trigger an hourly ban.** | **Primary launch feed** |
| **DexScreener API** `api.dexscreener.com` | Pair price, liquidity, volume, txns, market cap, FDV for graduated tokens | $0, no paid tier | No | 300 req/min (pairs/tokens/search), 60 req/min (profiles/boosts) | **Primary price + liquidity source** |
| **Jupiter lite-api** `lite-api.jup.ag` | Swap quotes, token prices (Price v3) | $0 | No | ~60 req/min quotes; keyless `api.jup.ag` is 0.5 req/s | Price fallback / executable-price check |
| **RugCheck.xyz** `api.rugcheck.xyz` | Risk score, risk flags, LP-lock %, top holders, creator + creator history, mint/freeze authority | $0 | No | Not publicly documented; 2–3 s gaps recommended between bulk queries | **Risk input** (one input, not the decision) |
| **Helius free** `mainnet.helius-rpc.com` | Solana RPC (account reads, mint info, DAS `getAsset`) + webhooks | $0 | Yes (signup) | 1M credits/mo, 10 req/s. Standard RPC = 1 credit, `getProgramAccounts` = 10, DAS = 10, Enhanced = 100 | **Primary on-chain reads** |
| **Solana public RPC** `api.mainnet-beta.solana.com` | Same RPC methods, shared | $0 | No | 100 req/10 s per IP total, 40 req/10 s per method. Docs say **not for production** | Fallback / dev only |
| **Raydium API v3** `api-v3.raydium.io` | Pool data, mint prices, liquidity for Raydium pools | $0 | No | Cloudflare per-IP limits, numbers unpublished; some endpoints 1–5 min stale | Post-graduation pool data |
| **Birdeye Data API** | Prices, OHLCV, holder data, token-security endpoint | $0 Standard: 30,000 CU/mo | Yes (signup) | 1 req/s on free. ≈750 price calls or ≈375 OHLCV calls/mo. Paid from $39/mo | **Backup only** — free allowance is too small to poll |
| **pump.fun official API** `api.pump.fun/v1` | Official developer API (68 routes, six scope families) | Unknown — key by application + team approval | Yes (Bearer, server-side only) | 2 req/s, 120-request bucket, 50,000/day | **Backup feed / reference only.** Whether it has a new-token route was NOT confirmed in this check — Stage 1 must verify before relying on it |
| **QuickNode** | Solana RPC | Free = **one-month trial only** (10M credits, 15 RPS; a Solana call costs 30 credits, so ≈333k calls), then $49/mo | Yes | — | **Excluded from the $0 plan.** Trial-only is not a foundation |

### The $0 stack (v1 runs on this, nothing else)

PumpPortal WS (launches) → DexScreener (price/liquidity) → RugCheck (risk flags) →
Helius free (on-chain truth: authorities, largest accounts) → Jupiter lite (price fallback).
Birdeye free and public RPC sit behind as fallbacks. Nothing in this stack costs money.

### What would cost money (flagged, not approved)

- Helius Developer: $49/mo (10M credits, 50 RPS) — first upgrade if the free 1M credits/mo runs short.
- Birdeye Lite: $39/mo (1.5–2.5M CU) — only if Birdeye becomes a primary source, which v1 does not need.
- QuickNode Build: $49/mo — not needed; trial-only free tier makes it a poor fit.
- SolanaTracker / Bitquery: paid indexed pump.fun data — not needed while PumpPortal's free feed works.
- Any paid RPC is a **decision for Cheli**, not a default. v1 is designed to fit inside the free tiers.

### Budget the tightest allowance

Helius free is the binding constraint at 1M credits/month. v1's RPC budget is capped at
**≈20,000 calls/day (≈600k/month)** so a normal month stays inside the free tier with
headroom. DexScreener's 300/min budget is far above what grading needs (a few calls per
minute), so it is not a constraint.

---

## 3. v1 scoring rule — fixed, no learning, no re-tuning

The rule is frozen once she approves it. It does not learn, retrain, or adapt.
If it fails the validation gate, it is killed, not re-tuned (same discipline as BTC v1).

### Universe and timing

- **Universe:** pump.fun launches only, seen on the PumpPortal new-token feed.
- **Pre-filter (free, from the event itself):** name and symbol non-empty; symbol ≤ 10
  characters; mint not seen before. This drops empty/junk spam before any paid-in-time
  check is spent.
- **Evaluation:** exactly once per token, at **age 10 minutes**. No re-entry, no second
  chances. A token is judged on what it looks like after surviving its first 10 minutes.
- **Signal cap:** at most **5 signals per day**, so the track record stays readable and
  the Discord is not spam. At that cap, 50 calls take at least 10 days; the 2-week
  minimum in the gate still binds.

### The score (0–10, signal at ≥ 7)

| # | Check | Source | Points |
|---|---|---|---|
| 1 | Dev holds ≤ 5% of supply at check time | RugCheck creator balance / top holders | +2 |
| 2 | Top-10 holders, excluding the bonding curve and pools, hold ≤ 30% | RugCheck top holders, or RPC `getTokenLargestAccounts` | +2 |
| 3 | RugCheck `score_normalised` ≤ 30 **and** no "danger" risk flags | RugCheck report | +2 |
| 4 | Graduated to an AMM pool with ≥ $10k liquidity | DexScreener / Raydium | +2 |
| 4a | *Instead of 4, if still on the curve:* bonding-curve progress ≥ 50% at 10 min | PumpPortal event fields | +1 |
| 5 | Socials present (X/Telegram/website in the metadata) | Token metadata | +1 |
| 6 | Creator has no prior token that rugged or died within 24 hours | RugCheck `creatorTokens` | +1 |

### Hard vetoes — any one kills the signal, whatever the score

- Dev holds **> 20%** of supply.
- Top-10 holders (ex-curve/ex-pool) hold **> 50%**.
- Freeze authority is active **and held by the creator's wallet** (not the program).
- Zero liquidity anywhere (no curve, no pool).

### Two things Stage 1 must get right (do not assume)

1. **Mint authority on pump.fun tokens is held by the pump program, not the creator.**
   "Mint authority exists" is therefore **not** a veto for pump.fun tokens — the check
   is *who* holds it. Stage 1 verifies the exact authority layout on a live mint
   (Helius DAS `getAsset`: `token_info.mint_authority` / `freeze_authority`) before
   coding this check. Getting this wrong would veto every legitimate launch.
2. **RugCheck's score is additive and new tokens score low by default.** One published
   test had a 2-minute-old memecoin scoring 1/100 risk — low because it had no history,
   not because it was safe. That is why check #3 is only 2 of 10 points and never the
   decision on its own. A low RugCheck score is **not** a safety verdict, and the signal
   must never be worded as one.

### Worked example

Token "MOONCAT" is 10 minutes old. Dev holds 3% (+2). Top-10 ex-curve hold 24% (+2).
RugCheck score 18, no danger flags (+2). Still on the curve at 62% progress (+1).
Has an X link (+1). Creator launched one prior token that is still trading (+1).
**Total: 9 → SIGNAL.** A token with dev holding 25% would be vetoed at any score.

---

## 4. Paper ledger — how a call is logged and graded

### The call lifecycle

1. **Signal** (age 10 min, score ≥ 7): log the call, post it to Discord + the public
   track record. Entry price = DexScreener pair price at signal time (Jupiter quote
   as fallback; if the token is still on the curve with no pair, Stage 1 freezes one
   price source for curve tokens before the run starts — the source must not change
   mid-run).
2. **Paper stake:** **$10 per call**, fixed, hypothetical. Every call is the same size
   so the record is comparable.
3. **Exits — first hit wins:**
   - Take profit: **+100%** (price doubles from entry).
   - Stop loss: **−50%** (price halves from entry).
   - Time stop: **24 hours** — grade at the 24-hour price whatever it is.
4. **Fee:** **1% on entry and 1% on exit (2% round trip).** This is the category
   standard — Trojan, Maestro, BONKbot, Photon, GMGN and Axiom all charge ~1% per
   trade (Banana Gun 0.5% manual / 1% snipes), all checked November 19, 2026.
5. **Grade:** net return = (exit ÷ entry − 1) − 0.02. Win = net return > 0.
   A call that doubles and exits pays 100% − 2% = +98%. A call stopped out pays
   −50% − 2% = −52%. A call that ends flat at 24h pays −2%.

### What the public track record shows (nothing hidden)

Every call, in order, with: mint, name/symbol, signal time, score + breakdown,
entry price + source, exit price, exit reason (TP / SL / 24h), net return.
Running totals underneath: calls so far, wins, win rate, cumulative net return after
fees, average net per call — and gate progress ("23 of 50 calls, day 12 of 14").
No call is ever removed or edited after grading. Losses stay visible.

### The gate, restated

**50 paper calls over at least 2 weeks, net positive after fees, every call posted
publicly.** Paid access stays closed until then. If the 50th call lands and net is
≤ 0, the rule is **KILLED** — not re-tuned, not given a second sample. A new rule
would be a new design, approved separately.

---

## 5. Architecture — mirroring BTC Oracle Pro

Same shape she already runs, same habits, no new platform to learn.

| Layer | BTC Oracle Pro (live) | The Meme Line (v1) |
|---|---|---|
| Service | Python service on Render free (`tracker.py`) | Python service on Render free, same shape |
| Durable history | Turso DB | Turso DB (free tier) |
| Capture loop | Per-minute hook (`capture.py`) | PumpPortal WebSocket client (one connection, reconnect with backoff) + a 60-second poller for evaluation/grading ticks |
| Public API | `/live`, `/live/all`, `/signals`, `/track-record`, `/health` | `/live` (latest signals), `/signals`, `/track-record`, `/health` |
| Dashboard | Static dashboard page (`static/`) | Static dashboard page: latest signals, public track record, gate progress |
| Posting | Hooks | Discord webhook for signals (alerts, not trades) |
| Deploy | `render.yaml`, deploy packages verified byte-for-byte from freshly re-fetched deployed files | Same pattern, same verification habit |

### Tables (Turso)

- `raw_launches` — every PumpPortal new-token event (mint, name, symbol, creator, timestamp, curve progress). The firehose log; never deleted.
- `evaluations` — one row per token evaluated at 10 min: score, per-check breakdown, veto flags, signal yes/no.
- `paper_calls` — the 50-call ledger: entry, exits, fee, net return, exit reason.
- `grades` — running totals snapshot (calls, wins, cumulative net), recomputed from `paper_calls`, never hand-edited.

### Build steps (Stage 1, after she approves this design)

0. **Keys and accounts.** Helius free key only (email signup, no card). No wallet, no SOL,
   no private key anywhere — ever. Render + Turso already exist from the BTC setup.
1. **Repo + schema.** Same layout as BTC (`tracker`-style service, `schema.sql`, `render.yaml`).
2. **Feed + raw log.** PumpPortal WS client; log every launch; prove the feed stays up for 24 hours.
3. **Pre-filter + evaluation tick.** 60-second poller evaluates tokens at age 10 min.
4. **Scoring + signal logging.** Frozen rule from §3; signals written to `paper_calls` and posted to Discord.
5. **Grading loop.** Price polls (DexScreener) until TP / SL / 24h; grades written, never edited.
6. **Public API + dashboard.** Track record page the public can read; gate progress visible.
7. **Two-week paper run + gate review.** The 50-call, 2-week bar from §4. She reviews the record; paid access only if it passes.
- Each step ends with a check; the next step does not start until the previous check passes.

### Cost — target $0/month

| Item | Cost |
|---|---|
| Render web service (free plan) | $0 |
| Turso DB (free tier) | $0 |
| Helius free (1M credits/mo) | $0 |
| DexScreener, RugCheck, Jupiter lite, PumpPortal, Raydium API | $0 |
| Discord webhook | $0 |
| **Total** | **$0/month** |

First paid step, if ever needed and only with her word: Helius Developer $49/mo.
Nothing else in v1 has a paid tier in its path.

### Known carry-over issues (from the BTC setup — same fixes apply)

- **Render free sleeps after ~15 minutes idle.** Same mitigation as BTC: a keepalive
  pinger. A sleeping service misses launches; the gap log must show it.
- **Poller silence.** The BTC capture had ~30-minute stretches where no tick ran at all,
  outside the capture code. The Meme Line needs the same gap logging: every missed
  minute recorded, and a missed evaluation window means that token is skipped, not
  backfilled and not guessed.
- **Rate-limit discipline.** A 429 from any source is a pause-and-retry, never a
  green light. A failed check logs as "unknown", never as a pass.

---

## 6. Risks — plain language

- **The speed moat is real, and we lose it on purpose.** Trojan ($24B lifetime volume),
  Photon ($45B+), Banana Gun, Maestro and the rest buy in the first seconds with
  infrastructure built for exactly that. We signal at 10 minutes. We will miss the
  fastest pumps. Anyone who wants to be first should not buy this product.
- **Most launches are scams or die within hours.** A 10-minute survivor filter helps,
  but it does not fix the base rate. Expect most paper calls to lose. The gate is
  "net positive after fees", not "usually right".
- **The feed is borrowed.** PumpPortal is unofficial, has no relationship with
  pump.fun, and already made its trade streams paid in May 2026. It can change terms,
  throttle, or ban. The fallback is the official pump.fun API (key + approval) or
  another community feed — Stage 1 keeps one fallback warm.
- **RugCheck is a black box.** Its score is one vendor's heuristics. It is an input,
  not a verdict, and a low score on a brand-new token means almost nothing.
- **Paper is optimistic.** Real trading adds pool fees (some memecoin pools charge
  1–4%), priority fees, slippage, and failed transactions on top of the 1% bot fee.
  Paper results will look better than real results. Say that on the track-record page.
- **DexScreener's terms.** Its API terms prohibit resale/redistribution and building a
  product whose primary purpose competes with DexScreener. Our use — showing prices in
  a track record — needs a read of the current terms at Stage 1 before launch.
- **Legal.** Terms review before the first paying member (same rule as BTC). No
  financial advice, 18+, risk language up front, no profit promises anywhere.
- **Key hygiene.** The only key in the system is a Helius *read* key: revocable, holds
  no funds. No wallet, no SOL, no private key in the workspace, in code, or in any
  chat — ever. If any step ever asks for a private key, that step is wrong; stop and
  ask her.

---

## 7. Open questions for Cheli

1. **Signal cap — max 5/day?** Proposed in §3. Keep it, raise it, lower it?
2. **Paper stake — $10 per call?** Proposed in §4. Fine, or a different fixed size?
3. **Evaluation window — 10 minutes, or graduation-only?** Graduation-only is safer
   and slower (fewer signals, more liquidity at entry). v1 as written covers both.
4. **Discord posting — bot auto-posts, or Cheli posts manually?** Auto-posting by
   webhook is the design; manual posting is slower but keeps her hands on every signal.
5. **Free public calls on X/Telegram — delayed?** The funnel posts some calls in
   public. Should public calls be delayed (e.g. 15 minutes) so paying members get
   them first, or same-time?
6. **Helius free key — who signs up?** Email-only signup, no card. She creates it, or
   Brio sets it up with her present? (Same caution as the Kalshi key: check the vault
   can hold the scheme *before* she creates anything — a read-only RPC key is a
   plain API key, unlike Kalshi's signed-request scheme.)
7. **If the gate fails (net ≤ 0 after 50 calls)** — kill the rule and stop, as the BTC
   precedent says, or allow one redesign? Recommendation: kill it, same as BTC.
8. **Track-record page name** — "The Meme Line" on the page, or a different public name?

---

## Appendix — source check log (November 19, 2026)

What was checked, and where. All figures are snapshots; re-check at Stage 1.

- **PumpPortal** — `wss://pumpportal.fun/api/data`; new-token + migration feeds free,
  no key; trade streams metered since May 2026 (API key + 0.02 SOL wallet, 0.01 SOL
  per 10,000 trades); one connection only, multiple = hourly ban. Sources:
  solanacompass.com/projects/pumpportal; crypto-claude-skills pumpfun docs.
- **DexScreener** — free, no key, no paid tier; 300 req/min pairs/tokens/search,
  60 req/min profiles/boosts; no OHLCV, no token-security data; ToS notes on resale
  and competing products. Sources: dxttools.trade/dexscreener/api;
  docs.dexscreener.com (via comparisons); coinpaprika.com comparison, checked
  18 Sep 2026.
- **Jupiter** — `lite-api.jup.ag` free, no key, ~60 req/min quotes; keyless
  `api.jup.ag` at 0.5 req/s; key via portal.jup.ag for production limits.
  Sources: developers.jup.ag/docs/swap; solana-api-skill Jupiter notes.
- **RugCheck.xyz** — `api.rugcheck.xyz/v1/tokens/{mint}/report`, free, no key,
  undocumented limits; `score_normalised` 0–100 (high = risky), `lpLockedPct`,
  `topHolders`, creator history. Additive-score caveat from a published 20 Aug 2026
  test (2-minute-old memecoin scored 1/100). Sources: trader.scorplabs.online
  rug-check guide; Xagent rugcheck skill. (Note: `rugcheck.aethercore.dev` is a
  different, paid service — not used.)
- **Helius** — free $0, 1M credits/mo, 10 RPS; standard RPC 1 credit,
  `getProgramAccounts` 10, DAS 10, Enhanced 100; paid from $49/mo (10M credits).
  Sources: instanodes.io RPC comparison; Helius pricing via nolimitnodes.com,
  checked 12 Nov 2026.
- **QuickNode** — free is a **one-month trial only** (10M credits, 15 RPS; Solana
  call = 30 credits); Build $49/mo (80M credits). Sources: quicknode.com pricing;
  instanodes.io; docs.rpcplane.dev.
- **Solana public RPC** — `api.mainnet-beta.solana.com`, no key; 100 req/10 s per IP,
  40 req/10 s per method; official docs: not for production. Sources:
  docs.rpcplane.dev; chainstack.com; Solana cluster docs via providers.skunkr.com.
- **Raydium API v3** — `api-v3.raydium.io`, free, no auth except Forum; Cloudflare
  per-IP limits (unpublished); some endpoints 1–5 min stale. Source: docs.raydium.io.
- **Birdeye** — Standard free $0, 30,000 CU/mo, 1 req/s, key required; ≈750 price
  calls or ≈375 OHLCV calls/mo; paid from $39/mo. Source: data.birdeye.so pricing,
  updated 17 Nov 2026.
- **pump.fun official API** — `api.pump.fun/v1`, Bearer key, application + team
  approval, 2 req/s, 120-request bucket, 50,000/day, 68 routes. Source:
  docs.pump.fun. New-token route not confirmed — Stage 1 check.
- **Bot fees** — 1% per trade is the category standard: Trojan 1%, Maestro 1%,
  BONKbot 1%, Photon 1%, GMGN 1%, Axiom ~1%, Banana Gun 0.5% manual / 1% snipes.
  Sources: trustscrypto.com (checked 9 Nov 2026); datawallet.com; odin.tools.
- **BTC Oracle Pro architecture** — Render free service (`tracker.py`, `render.yaml`),
  Turso durable history, per-minute capture hook, public API + static dashboard.
  Verified against `~/workspace/btc-oracle-pro/` on November 19, 2026.
