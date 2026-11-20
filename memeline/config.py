"""The Meme Line — frozen v1 constants (Stage 0 design, approved 2026-11-19).

These values are the approved rule. They do not change without her word.
If the rule fails the validation gate it is KILLED, not re-tuned.
"""
import os

# --- Scoring rule (design section 3) -------------------------------------
SIGNAL_SCORE_MIN = 7          # signal at score >= 7, out of 10
EVAL_AGE_SECONDS = 600        # evaluate exactly once, at age 10 minutes
EVAL_GRACE_SECONDS = 300      # ...but allow up to 15 min old (poller jitter/sleep)
DAILY_SIGNAL_CAP = 5          # at most 5 signals per day

# Check thresholds
DEV_MAX_PCT = 5.0             # check 1: dev holds <= 5% -> +2
DEV_VETO_PCT = 20.0           # veto: dev holds > 20%
TOP10_MAX_PCT = 30.0          # check 2: top-10 ex-curve/pool <= 30% -> +2
TOP10_VETO_PCT = 50.0         # veto: top-10 > 50%
RUGCHECK_MAX_SCORE = 30       # check 3: score_normalised <= 30, no danger flags -> +2
GRAD_MIN_LIQUIDITY_USD = 10_000.0  # check 4: graduated with >= $10k liquidity -> +2
CURVE_MIN_PROGRESS_PCT = 50.0      # check 4a: still on curve, >= 50% progress -> +1

# pump.fun bonding curve: the curve starts holding the token's FULL
# total supply (verified live 2026-11-20 on a real launch: curve
# balance 966,752,011 + 33,247,989 held outside = 1,000,000,000 =
# total supply, to the token). Progress = 1 - balance / total_supply.
# (An older figure of 793.1M circulates for the original curve design;
# it is WRONG for current launches — the smoke check proved the curve
# can hold more than 793.1M.)
CURVE_DECIMALS = 6

# --- Paper ledger (design section 4) --------------------------------------
STAKE_USD = 10.0              # fixed $10 per call, hypothetical
FEE_PER_SIDE = 0.01           # 1% entry, 1% exit (2% round trip)
TP_MULT = 2.0                 # take profit at +100%
SL_MULT = 0.5                 # stop loss at -50%
TIME_STOP_HOURS = 24          # grade at the 24-hour price whatever it is

# --- Validation gate (design section 4) ------------------------------------
GATE_CALLS = 50               # 50 paper calls
GATE_MIN_DAYS = 14            # over at least 2 weeks, net positive after fees

# --- Capture loop -----------------------------------------------------------
POLL_INTERVAL_SECONDS = 60    # evaluation/grading tick
WS_RECONNECT_BACKOFF = [5, 10, 20, 40, 60]  # seconds, capped at 60
RUGCHECK_MIN_GAP_SECONDS = 2.0  # pause between RugCheck queries
PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"

# --- Providers --------------------------------------------------------------
DEXSCREENER_BASE = "https://api.dexscreener.com"
RUGCHECK_BASE = "https://api.rugcheck.xyz"
JUPITER_LITE_BASE = "https://lite-api.jup.ag"
HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
SOLANA_PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"
HTTP_TIMEOUT_SECONDS = 20

# Graduation venues: a DexScreener pair on one of these means the token
# left the bonding curve for an AMM pool.
GRADUATION_DEX_IDS = {"raydium", "pumpswap", "orca", "meteora"}

# --- Environment ------------------------------------------------------------
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")  # user holds it; never in chat/files
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "memeline.db")
CAPTURE_ENABLED = os.environ.get("CAPTURE_ENABLED", "0") == "1"

VERSION = "1.0.0-stage1"
