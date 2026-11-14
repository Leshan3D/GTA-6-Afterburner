"""Dedicated SQLite database. Per-connection WAL, bounded history, no JAFAR files."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or utcnow()).isoformat()


class Storage:
    def __init__(self, filename: Path):
        self.path = Path(filename)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.setup()

    @contextmanager
    def conn(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=20000")
            connection.execute("PRAGMA journal_mode=WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def setup(self):
        with self.conn() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, collected_at TEXT NOT NULL,
                    market_json TEXT NOT NULL, dex_json TEXT NOT NULL, signal_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON snapshots(symbol, id DESC);
                CREATE TABLE IF NOT EXISTS news (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, headline TEXT NOT NULL,
                    publisher TEXT, url TEXT, published_at TEXT, first_seen TEXT NOT NULL,
                    family TEXT, asset_symbols_json TEXT NOT NULL, topic TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_news_seen ON news(first_seen DESC);
                CREATE TABLE IF NOT EXISTS health (
                    source TEXT PRIMARY KEY, state TEXT NOT NULL, message TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def set_health(self, source, state, message=""):
        with self.conn() as db:
            db.execute("INSERT INTO health VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET state=excluded.state,message=excluded.message,updated_at=excluded.updated_at",
                       (source, state, str(message)[:400], iso()))

    def health(self):
        with self.conn() as db:
            return [dict(row) for row in db.execute("SELECT * FROM health ORDER BY source")]

    def save_snapshot(self, symbol, market, dex, signal):
        # Keep derived metrics, not the entire repeated 100-bar OHLCV payload.
        # Otherwise a 3-minute polling loop inflates the database unnecessarily.
        market = {k:v for k,v in market.items() if k != "candles"}
        with self.conn() as db:
            db.execute("INSERT INTO snapshots(symbol,collected_at,market_json,dex_json,signal_json) VALUES(?,?,?,?,?)",
                       (symbol, iso(), json.dumps(market), json.dumps(dex), json.dumps(signal)))
            # Retain at most 7500 snapshots per symbol (~15 days at 3 min).
            db.execute("DELETE FROM snapshots WHERE symbol=? AND id NOT IN (SELECT id FROM snapshots WHERE symbol=? ORDER BY id DESC LIMIT 7500)", (symbol, symbol))

    def recent_snapshots(self, symbol, limit=2):
        with self.conn() as db:
            rows = db.execute("SELECT * FROM snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?", (symbol, limit)).fetchall()
            return [{"collected_at": row["collected_at"], "market": json.loads(row["market_json"]),
                     "dex": json.loads(row["dex_json"]), "signal": json.loads(row["signal_json"])} for row in rows]

    def history(self, symbol, limit=120):
        with self.conn() as db:
            rows = db.execute("SELECT collected_at,market_json,signal_json FROM snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?", (symbol, min(max(int(limit),1),300))).fetchall()
        return [{"ts": row["collected_at"], "price": json.loads(row["market_json"]).get("price"),
                 "state": json.loads(row["signal_json"]).get("state")} for row in reversed(rows)]

    def latest(self, symbols):
        return {sym: (self.recent_snapshots(sym, 1) or [None])[0] for sym in symbols}

    def insert_article(self, article):
        key = hashlib.sha256((article["source_id"] + "\n" + article["headline"].lower().strip()).encode()).hexdigest()
        with self.conn() as db:
            cursor = db.execute("INSERT OR IGNORE INTO news(id,source_id,headline,publisher,url,published_at,first_seen,family,asset_symbols_json,topic) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (key, article["source_id"], article["headline"], article.get("publisher"),
                                 article.get("url"), article.get("published_at"), iso(),
                                 article.get("family"), json.dumps(article.get("asset_symbols", [])), article.get("topic", "general")))
            return cursor.rowcount > 0

    def articles(self, hours=36, limit=100):
        since = iso(utcnow()-timedelta(hours=hours))
        with self.conn() as db:
            rows = db.execute("SELECT * FROM news WHERE first_seen >= ? ORDER BY first_seen DESC LIMIT ?", (since, limit)).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["asset_symbols"] = json.loads(d.pop("asset_symbols_json"))
            result.append(d)
        return result

    def seen_alert_recently(self, key, cooldown_seconds):
        with self.conn() as db:
            row = db.execute("SELECT sent_at FROM alerts WHERE alert_key=?", (key,)).fetchone()
        if not row:
            return False
        try:
            return (utcnow() - datetime.fromisoformat(row[0])).total_seconds() < cooldown_seconds
        except ValueError:
            return False

    def record_alert(self, key, payload):
        with self.conn() as db:
            db.execute("INSERT INTO alerts(alert_key,sent_at,payload) VALUES(?,?,?) ON CONFLICT(alert_key) DO UPDATE SET sent_at=excluded.sent_at,payload=excluded.payload",
                       (key, iso(), payload))

    def get_kv(self, key, default=None):
        with self.conn() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def put_kv(self, key, value):
        with self.conn() as db:
            db.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def recent_alerts(self, limit=5):
        with self.conn() as db:
            rows = db.execute("SELECT alert_key,sent_at FROM alerts ORDER BY sent_at DESC LIMIT ?",
                              (min(max(int(limit), 1), 20),)).fetchall()
        return [dict(row) for row in rows]
