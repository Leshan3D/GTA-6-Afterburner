"""Read-only operational reports for the private Telegram bot and CLI.

No network, broker, or execution calls. All displays are based on recorded data.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
import os
import json
from pathlib import Path


def seconds_since(stamp):
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            return None
        return max(0, (datetime.now(timezone.utc) - when).total_seconds())
    except (TypeError, ValueError, AttributeError):
        return None


def elapsed(stamp):
    seconds = seconds_since(stamp)
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 7200:
        return f"{int(seconds // 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def number(value, decimals=2, prefix=""):
    if isinstance(value, bool):
        return "n/a"
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return "n/a"
    if not math.isfinite(n):
        return "n/a"
    return f"{prefix}{n:,.{decimals}f}"


def status_report(assets, storage, config, command="/meme"):
    """Pure report formatting. Missing data stays missing; no synthetic prices."""
    pieces = command.strip().split(maxsplit=1)
    action = pieces[0].lower().split("@", 1)[0] if pieces else "/meme"
    argument = pieces[1].strip().upper() if len(pieces) > 1 else ""
    symbols = [a.symbol for a in assets]
    latest = storage.latest(symbols)
    max_age = config["schedule"]["max_market_age_seconds"]
    market_last = storage.get_kv("market_last_run")
    news_last = storage.get_kv("news_last_run")
    health = storage.health()

    if action in ("/help", "/start"):
        return ("🔥 AFTERBURNER | private status bot\n"
                "/meme — four-token overview\n"
                "/meme GATO — detailed token check (also DACITY, CYBERLEEK, GTA6)\n"
                "/progress — collector coverage and freshness\n"
                "/health — failed/degraded feeds\n"
                "/news — latest discovered headlines\n"
                "/status — service heartbeat\n"
                "/alerts — recent research alerts\n"
                "/link — configured public dashboard URL\n"
                "/publish — GitHub Pages snapshot publisher status\n"
                "Research only; no order execution.")

    if action == "/publish":
        from .config import data_dir
        path = data_dir() / "publisher" / "publisher-status.json"
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            return "📤 AFTERBURNER | PUBLISHER\nNot configured or not run yet."
        lines = ["📤 AFTERBURNER | GITHUB PAGES PUBLISHER",
                 "Last attempt: " + elapsed(state.get("last_attempt_at")),
                 "Last push: " + elapsed(state.get("last_push_at")),
                 "Last market sample published: " + elapsed(state.get("last_observation_at")),
                 "State: " + str(state.get("state", "unknown"))[:50],
                 "Site deployment: not verified by publisher"]
        if state.get("error"):
            lines.append("Last error: " + str(state["error"])[:140])
        return "\n".join(lines)

    if action == "/link":
        url = os.environ.get("AFTERBURNER_PUBLIC_URL", "").strip()
        if url.startswith("https://") and len(url) < 400:
            return "🌐 Public read-only AFTERBURNER dashboard:\n" + url
        return "Public URL is not configured. Set AFTERBURNER_PUBLIC_URL=https://... in ~/.config/afterburner/.env."

    if action == "/status":
        return ("🛰 AFTERBURNER | service report\n"
                f"Market run: {elapsed(market_last)}\n"
                f"News run: {elapsed(news_last)}\n"
                f"Process start: {elapsed(storage.get_kv('service_started_at'))}\n"
                f"Watchlist: {len(assets)} contracts\n"
                "Orders: DISABLED · Research only")

    if action == "/progress":
        fresh = sum(bool(s) and seconds_since(s["collected_at"]) is not None and
                    seconds_since(s["collected_at"]) <= max_age for s in latest.values())
        present = sum(bool(s) for s in latest.values())
        ok = sum(h["state"] == "ok" for h in health)
        degraded = len(health) - ok
        state = "🟢" if fresh == len(assets) and degraded == 0 else "🟡" if fresh else "🔴"
        # Deliberately call this coverage rather than false 'percent complete'.
        return (f"{state} AFTERBURNER | COLLECTION PROGRESS\n"
                f"Fresh markets: {fresh}/{len(assets)} (recorded: {present}/{len(assets)})\n"
                f"Data sources: {ok}/{len(health)} healthy; {degraded} degraded"
                + (" (not checked yet)" if not health else "") + "\n"
                f"Latest market cycle: {elapsed(market_last)}\n"
                f"Latest news cycle: {elapsed(news_last)}\n"
                f"Process start: {elapsed(storage.get_kv('service_started_at'))}\n"
                "Data freshness is observational, not signal accuracy.\n"
                "Execution: DISABLED")

    if action == "/health":
        bad = [h for h in health if h["state"] != "ok"]
        lines = [f"🩺 AFTERBURNER | FEED HEALTH — {len(bad)} degraded / {len(health)} checked"]
        if not health:
            lines.append("No feed checks recorded yet")
        elif not bad:
            lines.append("All recorded feeds report OK (not a guarantee of valid data)")
        else:
            for h in bad[:12]:
                lines.append(f"• {h['source']} — {h['state']} ({elapsed(h['updated_at'])})")
            if len(bad) > 12:
                lines.append(f"… plus {len(bad) - 12} other degraded feeds")
        return "\n".join(lines)

    if action == "/news":
        stories = storage.articles(hours=config["narrative"]["lookback_hours"], limit=5)
        lines = ["📰 AFTERBURNER | latest discovered headlines (unverified)"]
        if not stories:
            lines.append("No matching headlines recorded yet")
        for article in stories:
            lines.append(f"• {article['headline'][:155]} — {article['source_id']}")
        return "\n".join(lines)

    if action == "/alerts":
        alerts = storage.recent_alerts(5)
        lines = ["🔔 AFTERBURNER | recent research alerts"]
        if not alerts:
            lines.append("No alerts recorded")
        for alert in alerts:
            lines.append(f"• {alert['alert_key']} — {elapsed(alert['sent_at'])}")
        return "\n".join(lines)

    if action != "/meme":
        return "Unknown command. Send /help."
    if argument and argument not in {a.ticker for a in assets} | set(symbols):
        return f"Unknown token: {argument[:32]}. Try /meme GATO or /meme GTA6."

    if argument:
        a = next(a for a in assets if argument in (a.ticker, a.symbol))
        snap = latest.get(a.symbol)
        if not snap:
            return f"{a.ticker}: no market observations yet. Try /progress."
        m, sig, dex = snap["market"], snap["signal"], snap["dex"]
        is_stale = seconds_since(snap["collected_at"]) is None or seconds_since(snap["collected_at"]) > max_age
        return (f"🔥 AFTERBURNER | {a.ticker} {'⚠️ STALE' if is_stale else 'LIVE SNAPSHOT'}\n"
                f"Price: {number(m.get('price'), 8, '$')}\n"
                f"24h: {number(m.get('change_24h_pct'))}% | volume: {number(m.get('volume_24h_usd'), 0, '$')}\n"
                f"Funding: {number(m.get('funding_rate_pct'), 4)}% | OI: {number(m.get('open_interest_usd'), 0, '$')} (estimated)\n"
                f"BingX spread: {number(m.get('spread_pct'), 3)}%\n"
                f"DEX pool: {number((dex.get('primary') or {}).get('liquidity_usd'), 0, '$')}\n"
                f"Long {sig.get('long_score', 'n/a')}/100 | Short {sig.get('short_score', 'n/a')}/100\n"
                f"State: {sig.get('state', 'unknown')}\n"
                f"Blocked: {', '.join(sig.get('blocking_flags') or []) or 'none listed'}\n"
                f"Observed: {elapsed(snap['collected_at'])}\n"
                "Research only; no automatic trading.\n"
                f"https://bingx.com/en/perpetual/{a.symbol}")

    lines = ["🔥 AFTERBURNER | WATCHLIST · research only"]
    for a in assets:
        snap = latest.get(a.symbol)
        if not snap:
            lines.append(f"{a.ticker}: awaiting first sample")
            continue
        m, sig = snap["market"], snap["signal"]
        stale = " ⚠️STALE" if seconds_since(snap["collected_at"]) is None or seconds_since(snap["collected_at"]) > max_age else ""
        lines.append(f"{a.ticker}: {number(m.get('price'), 8, '$')} · L{sig.get('long_score','?')} S{sig.get('short_score','?')} · {sig.get('state','UNKNOWN')}{stale}")
    lines.append("/progress for feed coverage · /help for commands")
    return "\n".join(lines)
