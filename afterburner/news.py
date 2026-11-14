"""GTA narrative RSS discovery using stdlib XML; game coverage isn't a trade signal."""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import xml.etree.ElementTree as ET
from .storage import iso, utcnow

TOPIC = re.compile(r"\b(?:GTA\s*(?:6|VI)|Grand Theft Auto|Rockstar|Dacity|CyberLeek|GTA6CAT)\b", re.I)
CATALYSTS = ["delay", "postpone", "trailer", "launch", "release", "preload", "leak", "gameplay", "earnings", "rockstar", "review"]


def classify_title(title, assets):
    topic = TOPIC.search(title) is not None
    tags = ["gta6_narrative"] if topic else []
    for asset in assets:
        if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", title, re.I)
               for alias in asset.news_aliases):
            tags.append(asset.symbol)
    event = next((keyword for keyword in CATALYSTS if keyword in title.casefold()), "general")
    return topic or bool(tags), tags, event


def parsed_date(raw):
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


def parse_feed(raw):
    """Return list of normalized RSS2/Atom entries or raise on malformed XML."""
    root = ET.fromstring(raw)
    rows = []
    for entry in list(root.findall(".//item")) + list(root.findall(".//{http://www.w3.org/2005/Atom}entry")):
        def get(tag):
            node = entry.find(tag)
            return (node.text or "").strip() if node is not None else ""
        title = get("title") or get("{http://www.w3.org/2005/Atom}title")
        url = get("link")
        if not url:
            atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
            url = atom_link.attrib.get("href", "") if atom_link is not None else ""
        date = get("pubDate") or get("{http://www.w3.org/2005/Atom}published") or get("{http://www.w3.org/2005/Atom}updated")
        pub = get("source") or get("{http://www.w3.org/2005/Atom}author")
        rows.append({"title": title, "link": url, "published_at": parsed_date(date), "publisher": pub})
    return rows


class News:
    def __init__(self, http, storage):
        self.http, self.storage = http, storage

    def update(self, sources, assets):
        counts = {"new": 0, "read": 0, "failures": 0}
        for feed in sources:
            try:
                raw = self.http.get(feed["url"], response_type="text")
                entries = parse_feed(raw)
                for entry in entries[:60]:
                    title = entry["title"][:300]
                    ok, tags, topic = classify_title(title, assets)
                    if not ok:
                        continue
                    # Never turn an old headline into a fresh trigger on first sync.
                    date = entry.get("published_at")
                    if date and abs((utcnow() - datetime.fromisoformat(date)).total_seconds()) > 48*3600:
                        continue
                    counts["read"] += 1
                    article = {"source_id": feed["id"], "headline": title,
                               "publisher": entry.get("publisher"),
                               "url": entry["link"][:1200], "published_at": date,
                               "family": feed.get("family"), "asset_symbols": tags, "topic": topic}
                    if self.storage.insert_article(article):
                        counts["new"] += 1
                self.storage.set_health("news:"+feed["id"], "ok", f"Parsed {len(entries)} items")
            except Exception as exc:
                counts["failures"] += 1
                self.storage.set_health("news:"+feed["id"], "error", str(exc))
        self.storage.put_kv("news_last_run", iso())
        return counts
