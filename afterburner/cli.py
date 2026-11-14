"""CLI bootstrap; has no private exchange or order placing capabilities."""
import argparse
import logging
import sys
import os
import requests
from .config import ROOT, assets, data_dir, load_env_file, settings, sources
from .service import Radar
from .public import export_public_site


def main(argv=None):
    parser = argparse.ArgumentParser(description="AFTERBURNER independent meme futures intelligence (read-only)")
    parser.add_argument("command", choices=["doctor", "once", "serve", "news", "market", "status",
                                            "progress", "bot-chats", "export-public", "signals", "setups", "performance", "kronos", "backtest"])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output", default=None, help="Export destination for GitHub Pages static site")
    opts = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if opts.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_env_file()
    if opts.command == "doctor":
        print("AFTERBURNER v0.4.0 / static configuration check (no external API calls)")
        conf = settings()
        watch = assets()
        sources()
        location = data_dir()
        for asset in watch:
            print(f"{asset.symbol} candidate_mint={asset.candidate_mint} verified={asset.mint_verified}")
        print(f"Project: {ROOT}\nDatabase directory: {location}")
        print(f"Private dashboard: http://{conf['server']['host']}:{conf['server']['port']}/")
        if conf.get("public", {}).get("enabled"):
            print(f"Public-safe preview: http://127.0.0.1:{conf['public']['port']}/")
        print("Order execution: NOT IMPLEMENTED / NOT POSSIBLE")
        return 0
    if opts.command == "bot-chats":
        # Use this only while the systemd service is stopped to avoid concurrent
        # getUpdates polling. Does not print or log the bot token.
        token = os.getenv("AFTERBURNER_TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            print("Bot token missing in ~/.config/afterburner/.env", file=sys.stderr)
            return 2
        try:
            response = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                    params={"timeout": 0, "limit": 40}, timeout=10)
            response.raise_for_status()
            if not response.json().get("ok"):
                print("Telegram returned an unsuccessful result", file=sys.stderr)
                return 2
            chats = {str(u.get("message", {}).get("chat", {}).get("id"))
                     for u in response.json().get("result", [])
                     if u.get("message", {}).get("chat", {}).get("id") is not None}
            print("Recent chat IDs:", ", ".join(sorted(chats)) or "none — send /start to the bot first")
            return 0
        except requests.RequestException:
            print("Telegram request failed; check internet and bot token", file=sys.stderr)
            return 2
    radar = Radar()
    if opts.command == "export-public":
        dest = export_public_site(radar.storage, radar.assets, radar.config, opts.output)
        print(f"Public static snapshot exported: {dest} (commit/push to update GitHub Pages)")
        return 0
    if opts.command in ('signals','setups','performance','kronos'):
        print(radar.status_text('/'+opts.command))
        return 0
    if opts.command == 'backtest':
        from .backtest import backtest
        import json
        for asset in radar.assets:
            bars=radar.storage.get_candles('bingx',asset.symbol,'5m',5000)
            result=backtest(asset.symbol,bars,radar.config)
            print(json.dumps(result,ensure_ascii=False))
        return 0
    if opts.command == "once":
        print("News:", radar.poll_news())
        print("Markets:", radar.poll_market())
        print(radar.status_text())
    elif opts.command == "news":
        print(radar.poll_news())
    elif opts.command == "market":
        print(radar.poll_market())
    elif opts.command == "status":
        print(radar.status_text("/status"))
    elif opts.command == "progress":
        print(radar.status_text("/progress"))
    elif opts.command == "serve":
        radar.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
