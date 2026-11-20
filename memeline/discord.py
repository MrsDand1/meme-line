"""The Meme Line — Discord webhook posting (signals only, never trades).

Plain English, every call tracked wins and losses, no profit promises.
If no webhook is configured this is a silent no-op.
"""
import requests

from . import config


def signal_message(call, evaluation):
    """Build the plain-English signal text for one paper call."""
    mint = call["mint"]
    checks = "\n".join(
        f"- {c['label']}: {'+' + str(c['points']) if c['points'] else '0'} "
        f"({c['status']}) — {c['detail']}"
        for c in evaluation["checks"]
    )
    return (
        "The Meme Line — paper signal (not a trade, not financial advice)\n"
        f"Token: {call.get('name', '')} ({call.get('symbol', '')})\n"
        f"Mint: {mint}\n"
        f"Score: {evaluation['score']}/10 — signal at 7+\n"
        f"Entry: ${call['entry_price']:.8g} ({call['entry_source']}) — "
        f"paper stake ${config.STAKE_USD:.0f}\n"
        f"Take profit +100% / Stop loss -50% / 24-hour time stop\n"
        "Score breakdown:\n"
        f"{checks}\n"
        "Most memecoins lose money. This is a paper signal for learning — "
        "check it yourself before you ever risk real money."
    )


def post_signal(webhook_url, call, evaluation):
    """Post one signal to Discord. Returns True if posted."""
    if not webhook_url:
        return False
    try:
        r = requests.post(
            webhook_url,
            json={"content": signal_message(call, evaluation)},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        return r.status_code in (200, 204)
    except requests.RequestException:
        return False
