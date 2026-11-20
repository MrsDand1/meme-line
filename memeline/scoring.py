"""The Meme Line — frozen v1 scoring rule (design section 3).

Pure functions: no network, no database, no clocks. The rule is frozen
once approved. It does not learn, retrain, or adapt. If it fails the
validation gate it is killed, not re-tuned.

A failed check is logged as "unknown" and scores 0 points — never a pass.
A veto kills the signal whatever the score.
"""
from . import config


def prefilter(event):
    """Free pre-filter on the PumpPortal event itself.

    Returns (ok, reason). Drops empty/junk spam before any check is spent.
    The universe is pump.fun launches only: when the event carries a
    `pool` field it must read "pump".
    """
    name = (event.get("name") or "").strip()
    symbol = (event.get("symbol") or "").strip()
    if not name:
        return False, "empty name"
    if not symbol:
        return False, "empty symbol"
    if len(symbol) > 10:
        return False, "symbol longer than 10 characters"
    if not event.get("mint"):
        return False, "no mint"
    pool = event.get("pool")
    if pool and pool != "pump":
        return False, f"not a pump.fun launch (pool={pool})"
    return True, ""


def top10_ex_curve(top_holders, exclude_addresses):
    """Sum of the top-10 holders' pct, excluding curve/pool accounts.

    top_holders: list of {"address": str, "pct": float} sorted desc by pct.
    exclude_addresses: set of addresses to ignore (bonding-curve token
    account, AMM pool vaults). Returns (pct, excluded_count).
    """
    excluded = set(exclude_addresses or [])
    kept = [h for h in (top_holders or []) if h.get("address") not in excluded]
    pct = sum(float(h.get("pct") or 0) for h in kept[:10])
    return pct, len(top_holders or []) - len(kept)


def score(evidence):
    """Score one token from gathered evidence. Returns a result dict.

    evidence fields (None = could not be verified -> "unknown", 0 points):
      dev_pct, top10_pct_ex, rugcheck_score, rugcheck_danger (bool),
      graduated (bool), liquidity_usd, curve_progress_pct,
      has_socials (bool), creator_clean (bool|None),
      freeze_authority (str|None), creator (str|None),
      has_market (bool) — any price source at all (pair or quote).
    """
    checks = []
    vetoes = []
    unknowns = []

    def check(cid, label, points, status, detail):
        checks.append({"id": cid, "label": label, "points": points,
                       "status": status, "detail": detail})
        if status == "unknown":
            unknowns.append(cid)

    # --- Check 1: dev holds <= 5% ------------------------------------------
    dev = evidence.get("dev_pct")
    if dev is None:
        check(1, "Dev holds <= 5% of supply", 0, "unknown", "dev holding not verified")
    elif dev <= config.DEV_MAX_PCT:
        check(1, "Dev holds <= 5% of supply", 2, "pass", f"dev holds {dev:.2f}%")
    else:
        check(1, "Dev holds <= 5% of supply", 0, "fail", f"dev holds {dev:.2f}%")

    # --- Check 2: top-10 ex-curve/pool <= 30% ------------------------------
    top10 = evidence.get("top10_pct_ex")
    if top10 is None:
        check(2, "Top-10 holders (ex-curve/pool) <= 30%", 0, "unknown",
              "holder distribution not verified")
    elif top10 <= config.TOP10_MAX_PCT:
        check(2, "Top-10 holders (ex-curve/pool) <= 30%", 2, "pass",
              f"top-10 hold {top10:.2f}%")
    else:
        check(2, "Top-10 holders (ex-curve/pool) <= 30%", 0, "fail",
              f"top-10 hold {top10:.2f}%")

    # --- Check 3: RugCheck score <= 30, no danger flags --------------------
    # A low RugCheck score on a brand-new token means almost nothing
    # (additive score, new tokens score low by default) — so this is only
    # 2 of 10 points and never the decision on its own.
    rc_score = evidence.get("rugcheck_score")
    rc_danger = bool(evidence.get("rugcheck_danger"))
    if rc_score is None:
        check(3, "RugCheck score <= 30, no danger flags", 0, "unknown",
              "RugCheck report not available")
    elif rc_score <= config.RUGCHECK_MAX_SCORE and not rc_danger:
        check(3, "RugCheck score <= 30, no danger flags", 2, "pass",
              f"RugCheck score {rc_score}, no danger flags")
    else:
        detail = f"RugCheck score {rc_score}"
        if rc_danger:
            detail += ", danger flag present"
        check(3, "RugCheck score <= 30, no danger flags", 0, "fail", detail)

    # --- Check 4 / 4a: graduated liquidity, or curve progress --------------
    graduated = bool(evidence.get("graduated"))
    liq = evidence.get("liquidity_usd")
    curve = evidence.get("curve_progress_pct")
    if graduated:
        if liq is None:
            check(4, "Graduated with >= $10k liquidity", 0, "unknown",
                  "graduated but liquidity not verified")
        elif liq >= config.GRAD_MIN_LIQUIDITY_USD:
            check(4, "Graduated with >= $10k liquidity", 2, "pass",
                  f"liquidity ${liq:,.0f}")
        else:
            check(4, "Graduated with >= $10k liquidity", 0, "fail",
                  f"liquidity ${liq:,.0f}")
    else:
        if curve is None:
            check("4a", "On curve, >= 50% bonding-curve progress", 0, "unknown",
                  "curve progress not verified")
        elif curve >= config.CURVE_MIN_PROGRESS_PCT:
            check("4a", "On curve, >= 50% bonding-curve progress", 1, "pass",
                  f"curve {curve:.0f}% complete")
        else:
            check("4a", "On curve, >= 50% bonding-curve progress", 0, "fail",
                  f"curve {curve:.0f}% complete")

    # --- Check 5: socials present -------------------------------------------
    if evidence.get("has_socials"):
        check(5, "Socials present (X / Telegram / website)", 1, "pass",
              "social links found in token metadata")
    else:
        check(5, "Socials present (X / Telegram / website)", 0, "fail",
              "no social links found")

    # --- Check 6: creator has no prior rugged / dead-in-24h token ----------
    clean = evidence.get("creator_clean")
    if clean is None:
        check(6, "Creator has no prior rugged token", 0, "unknown",
              "creator history not verified")
    elif clean:
        check(6, "Creator has no prior rugged token", 1, "pass",
              "no prior rugged or dead token from this creator")
    else:
        check(6, "Creator has no prior rugged token", 0, "fail",
              "creator has a prior rugged or dead token")

    # --- Hard vetoes ---------------------------------------------------------
    if dev is not None and dev > config.DEV_VETO_PCT:
        vetoes.append(f"dev holds {dev:.2f}% (> {config.DEV_VETO_PCT:.0f}%)")
    if top10 is not None and top10 > config.TOP10_VETO_PCT:
        vetoes.append(f"top-10 hold {top10:.2f}% (> {config.TOP10_VETO_PCT:.0f}%)")
    freeze = evidence.get("freeze_authority")
    creator = evidence.get("creator")
    if freeze and creator and freeze == creator:
        vetoes.append("freeze authority is active and held by the creator")
    if not evidence.get("has_market", True):
        vetoes.append("zero liquidity anywhere (no pair, no quote, no curve)")

    total = sum(c["points"] for c in checks)
    signal = total >= config.SIGNAL_SCORE_MIN and not vetoes
    return {
        "score": total,
        "checks": checks,
        "vetoes": vetoes,
        "unknowns": unknowns,
        "signal": signal,
    }
