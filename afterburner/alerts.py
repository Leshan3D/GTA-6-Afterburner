"""Private Telegram commands and research alerts; independent of JAFAR credentials.

Never reads other services' tokens. Whitelisted chats only; unknown chats silently ignored.
Telegram bot polling runs in its own thread, keeping /progress responsive during
slow public-market collection. No trade execution or command shell hooks.
"""
from __future__ import annotations
import logging
import os
import re
import requests

LOG = logging.getLogger(__name__)
COMMAND = re.compile(r"^/([a-zA-Z]+)(?:@([a-zA-Z0-9_]+))?(?:\s+(.{1,120}))?$", re.S)
KNOWN_COMMANDS = frozenset(("meme", "status", "help", "start", "progress", "health", "news", "alerts", "link", "publish", "signals", "signal", "setups", "performance", "kronos"))


def parse_command(text, bot_username=""):
    """Telegram /cmd@Bot arg form; reject commands addressed to another bot."""
    if not isinstance(text, str):
        return None
    match = COMMAND.fullmatch(text.strip())
    if not match or match.group(1).lower() not in KNOWN_COMMANDS:
        return None
    cmd, mentioned, argument = match.groups()
    if mentioned and (not bot_username or mentioned.lower() != bot_username.lstrip("@").lower()):
        return None
    return "/" + cmd.lower() + (" " + argument.strip() if argument else "")


class Telegram:
    def __init__(self, storage):
        self.storage = storage
        self.token = os.getenv("AFTERBURNER_TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("AFTERBURNER_TELEGRAM_CHAT_ID", "").strip()
        extras = os.getenv("AFTERBURNER_TELEGRAM_ALLOWED_CHAT_IDS", "")
        self.allowed_chat_ids = {x.strip() for x in extras.split(",") if x.strip()}
        if self.chat_id:
            self.allowed_chat_ids.add(self.chat_id)
        self.bot_username = os.getenv("AFTERBURNER_TELEGRAM_BOT_USERNAME", "").strip()
        self.enabled = bool(self.token and self.allowed_chat_ids)
        self.push_enabled = bool(self.token and self.chat_id and os.getenv("AFTERBURNER_NO_ALERTS", "0") != "1")

    def send_to(self, chat_id, message):
        """Send only to an explicitly allowlisted chat; no markdown/HTML parsing."""
        if not self.token or str(chat_id) not in self.allowed_chat_ids:
            return False
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": str(chat_id), "text": str(message)[:3800], "disable_web_page_preview": True},
                timeout=12,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("Telegram API rejected the send")
            return True
        except (requests.RequestException, ValueError) as exc:
            LOG.warning("Telegram send failed: %s", str(exc).replace(self.token, "[REDACTED]"))
            self.storage.set_health("telegram:send", "error", "Send failed")
            return False

    def send(self, message):
        """Push messages go to the primary operator only, not all allowed friends."""
        return self.push_enabled and self.send_to(self.chat_id, message)

    def signal(self, asset, snapshot, cooldown_seconds):
        if not self.push_enabled:
            return False
        sig = snapshot["signal"]
        if sig["state"] == "MONITOR":
            return False
        key = asset.symbol + ":" + sig["state"] + ":" + sig["bias"]
        if self.storage.seen_alert_recently(key, cooldown_seconds):
            return False
        market = snapshot["market"]
        p = market.get("price")
        price = f"${p:.8f}" if isinstance(p, (int, float)) else "unavailable"
        blocked = ", ".join(sig["blocking_flags"][:4]) or "none"
        message = (f"🔥 AFTERBURNER | {asset.symbol}\n"
                   f"{sig['state']} — {sig['bias']}\n"
                   f"Price: {price}\nLong {sig['long_score']}/100 | Short {sig['short_score']}/100\n"
                   f"Flags: {blocked}\n"
                   f"Game news count (36h): {sig['narrative']['game_articles_36h']}\n"
                   "Research alert only; verify market and define risk manually.\n"
                   f"https://bingx.com/en/perpetual/{asset.symbol}")
        if self.send(message):
            self.storage.record_alert(key, message)
            return True
        return False

    def technical_signal(self, setup, signal_id):
        """Notify only once per completed candle; never sends execution commands."""
        if not self.push_enabled:return False
        key=f"tech:{setup['symbol']}:{setup['timeframe']}:{setup['as_of_candle_ts']}"
        if self.storage.seen_alert_recently(key, 86400):return False
        message=(f"🚦 AFTERBURNER | {setup['symbol']} {setup['direction']} SIGNAL #{signal_id}\n"
                 f"Timeframe: {setup['timeframe']} | Confidence index: {setup['confidence']}/100\n"
                 f"Trigger: {setup['entry_trigger']:.8f}\nStop: {setup['sl']:.8f}\n"
                 f"TP1: {setup['tp1']:.8f} | TP2: {setup['tp2']:.8f}\n"
                 f"Net estimated R:R {setup['rr']:.2f}\n"
                 "Confirmed candle, NOT a filled trade. No automatic execution.\n"
                 f"https://bingx.com/en/perpetual/{setup['symbol']}")
        if self.send(message):
            self.storage.record_alert(key,message)
            return True
        return False

    def poll_once(self, status_text):
        if not self.enabled:
            return 0
        handled = 0
        try:
            after = int(self.storage.get_kv("telegram_offset", "0"))
            response = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"timeout": 5, "offset": after, "limit": 30, "allowed_updates": '["message"]'},
                timeout=11,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise ValueError("Telegram getUpdates not ok")
            for item in body.get("result", []):
                update_id = int(item["update_id"])
                msg = item.get("message") or {}
                chat = msg.get("chat") or {}
                recipient = str(chat.get("id", ""))
                command = parse_command(msg.get("text"), self.bot_username)
                if recipient in self.allowed_chat_ids and command:
                    answer = status_text(command) if callable(status_text) else str(status_text)
                    if self.send_to(recipient, answer):
                        handled += 1
                # Always advance offset, including unknown/unauthorized messages.
                self.storage.put_kv("telegram_offset", update_id + 1)
            self.storage.set_health("telegram:poll", "ok", "Polling operational")
            return handled
        except Exception as exc:
            LOG.warning("Telegram poll failed: %s", str(exc).replace(self.token, "[REDACTED]"))
            self.storage.set_health("telegram:poll", "error", "Polling failed")
            return handled

    def poll_forever(self, status_text, stop_event):
        if not self.enabled:
            LOG.info("Telegram bot disabled (no token or allowlisted chat ID)")
            return
        LOG.info("Private Telegram status bot active for %d allowlisted chat(s)", len(self.allowed_chat_ids))
        while not stop_event.is_set():
            self.poll_once(status_text)
            stop_event.wait(3)
