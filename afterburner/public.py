"""A sanitized, separate read-only public dashboard and GitHub Pages snapshot exporter.

This module deliberately has no access to credentials, trading endpoints, health
error strings, Telegram chats, filesystem metadata or private operational routes.
The public preview always binds to loopback and can be exposed by an opt-in tunnel.
"""
from __future__ import annotations
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import math
from urllib.parse import urlparse
from .config import ROOT
from .storage import utcnow

INDEX = (ROOT / "site" / "index.html")


def safe_number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def public_payload(storage, assets, config):
    """Explicit allowlist only. New internal DB fields cannot leak implicitly."""
    latest = storage.latest([a.symbol for a in assets])
    now = utcnow()
    limit = config["schedule"]["max_market_age_seconds"]
    records = []
    for asset in assets:
        snapshot = latest.get(asset.symbol)
        if snapshot:
            market, sig, dex = snapshot["market"], snapshot["signal"], snapshot["dex"]
            try:
                observed = datetime.fromisoformat(snapshot["collected_at"])
                stale = observed.tzinfo is None or (now - observed).total_seconds() > limit
            except (TypeError, ValueError):
                stale = True
            primary = dex.get("primary") or {}
            hist = storage.history(asset.symbol, 48)
        else:
            market = sig = primary = {}
            stale = True
            hist = []
        records.append({
            "symbol": asset.symbol,
            "ticker": asset.ticker,
            "label": asset.label,
            "observed_at": snapshot["collected_at"] if snapshot else None,
            "stale_at_export": stale,
            "stale_after_seconds": limit,
            "contract_active": bool(market.get("contract_active")),
            "price": safe_number(market.get("price")),
            "change_24h_pct": safe_number(market.get("change_24h_pct")),
            "volume_24h_usd": safe_number(market.get("volume_24h_usd")),
            "funding_rate_pct": safe_number(market.get("funding_rate_pct")),
            "spread_pct": safe_number(market.get("spread_pct")),
            "dex_liquidity_usd": safe_number(primary.get("liquidity_usd")),
            "long_score": sig.get("long_score"),
            "short_score": sig.get("short_score"),
            "research_state": sig.get("state") if not stale else "STALE",
            "blocking_flags": [str(x)[:90] for x in (sig.get("blocking_flags") or [])[:6]],
            "mint_verified": bool(asset.mint_verified),
            "trade_url": f"https://bingx.com/en/perpetual/{asset.symbol}",
            "history": [{"ts": x["ts"], "price": safe_number(x.get("price"))} for x in hist],
        })
    news = []
    for article in storage.articles(hours=config["narrative"]["lookback_hours"], limit=12):
        raw_url = str(article.get("url") or "")
        url = raw_url if urlparse(raw_url).scheme == "https" else None
        news.append({"headline": str(article.get("headline") or "")[:200],
                     "source": str(article.get("source_id") or "")[:60],
                     "url": url,
                     "first_seen": article.get("first_seen")})
    return {"product": "AFTERBURNER Public Radar", "generated_at": now.isoformat(),
            "execution_enabled": False, "research_only": True, "assets": records, "news": news}


def export_public_site(storage, assets, config, destination=None):
    """Static GitHub Pages snapshot. Re-export and git push to refresh the public site."""
    target = Path(destination or ROOT / "site").expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    data = public_payload(storage, assets, config)
    html = INDEX.read_bytes()
    # Copy the template for custom output folders; default index stays unchanged.
    if (target / "index.html").resolve() != INDEX.resolve():
        (target / "index.html").write_bytes(html)
    (target / ".nojekyll").touch()
    tmp = target / ".data.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    tmp.replace(target / "data.json")
    return target


def build_public_server(storage, assets, config):
    public = config.get("public", {})
    host, port = public.get("host", "127.0.0.1"), int(public.get("port", 5057))
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Public preview must bind to loopback; use an opt-in secure tunnel")
    if port == int(config["server"]["port"]):
        raise ValueError("Public preview and private dashboard require separate ports")

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, payload, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                return self.respond(200, INDEX.read_bytes(), "text/html")
            if route == "/data.json":
                data = json.dumps(public_payload(storage, assets, config), ensure_ascii=False, allow_nan=False).encode()
                return self.respond(200, data, "application/json")
            return self.respond(404, b"Not found", "text/plain")

        def log_message(self, fmt, *args):
            import logging
            logging.getLogger("afterburner.public").info(fmt, *args)

    return ThreadingHTTPServer((host, port), Handler)
