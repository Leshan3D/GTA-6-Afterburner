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
                CREATE TABLE IF NOT EXISTS ohlcv_candles (
                  venue TEXT NOT NULL, instrument TEXT NOT NULL, timeframe TEXT NOT NULL,
                  open_ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
                  low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                  collected_at TEXT NOT NULL,
                  PRIMARY KEY(venue,instrument,timeframe,open_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_ohlcv_recent ON ohlcv_candles(instrument,timeframe,open_ts DESC);
                CREATE TABLE IF NOT EXISTS signals (
                  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT NOT NULL,
                  state TEXT NOT NULL, timeframe TEXT NOT NULL, as_of_candle_ts INTEGER NOT NULL,
                  strategy_version TEXT NOT NULL, created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, details_json TEXT NOT NULL,
                  UNIQUE(symbol,timeframe,strategy_version,as_of_candle_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_sig_state ON signals(state,created_at DESC);
                CREATE TABLE IF NOT EXISTS signal_events (
                  id INTEGER PRIMARY KEY, signal_id INTEGER NOT NULL REFERENCES signals(id),
                  event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
                  price REAL, note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_fills (
                  signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
                  filled_at TEXT NOT NULL, fill_price REAL NOT NULL,
                  closed_at TEXT, exit_price REAL, exit_reason TEXT, net_pnl_pct REAL
                );
                CREATE TABLE IF NOT EXISTS kronos_forecasts (
                  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                  cutoff_ts INTEGER NOT NULL, horizon_bars INTEGER NOT NULL,
                  model_version TEXT NOT NULL, created_at TEXT NOT NULL,
                  forecast_json TEXT NOT NULL, actual_return_pct REAL,
                  UNIQUE(symbol,timeframe,cutoff_ts,horizon_bars,model_version)
                );
                CREATE TABLE IF NOT EXISTS backtest_runs (
                  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                  symbol TEXT NOT NULL, timeframe TEXT NOT NULL, strategy_version TEXT NOT NULL,
                  results_json TEXT NOT NULL
                );
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


    def save_candles(self, venue, symbol, timeframe, candles, now_ms=None):
        """Insert only validated and closed bars, idempotently."""
        from .signal_engine import valid_candles, TIMEFRAMES
        now_ms = now_ms or int(utcnow().timestamp()*1000)
        valid = [x for x in valid_candles(candles, timeframe)
                 if x['ts'] + TIMEFRAMES[timeframe]*1000 <= now_ms]
        with self.conn() as db:
            for x in valid:
                db.execute("""INSERT INTO ohlcv_candles
                    (venue,instrument,timeframe,open_ts,open,high,low,close,volume,collected_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                    (venue,symbol,timeframe,x['ts'],x['open'],x['high'],x['low'],x['close'],x['volume'],iso()))
        return len(valid)

    def get_candles(self, venue, symbol, timeframe, limit=500):
        with self.conn() as db:
            rows=db.execute("""SELECT open_ts,open,high,low,close,volume FROM ohlcv_candles
                WHERE venue=? AND instrument=? AND timeframe=? ORDER BY open_ts DESC LIMIT ?""",
                (venue,symbol,timeframe,min(5000,max(1,int(limit))))).fetchall()
        return [{'ts':r['open_ts'],'open':r['open'],'high':r['high'],'low':r['low'],
                 'close':r['close'],'volume':r['volume']} for r in reversed(rows)]

    def save_setup(self, signal):
        if not signal.get('as_of_candle_ts'):
            return None
        with self.conn() as db:
            db.execute("""INSERT INTO signals
                (symbol,direction,state,timeframe,as_of_candle_ts,strategy_version,created_at,updated_at,details_json)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,strategy_version,as_of_candle_ts)
                DO UPDATE SET direction=excluded.direction,state=excluded.state,updated_at=excluded.updated_at,details_json=excluded.details_json""",
                (signal['symbol'],signal['direction'],signal.get('state','FORMING'),signal['timeframe'],
                 signal['as_of_candle_ts'],signal['strategy_version'],iso(),iso(),
                 json.dumps(signal,allow_nan=False)))
            row=db.execute("""SELECT id FROM signals WHERE symbol=? AND timeframe=?
                AND strategy_version=? AND as_of_candle_ts=?""",
                (signal['symbol'],signal['timeframe'],signal['strategy_version'],signal['as_of_candle_ts'])).fetchone()
            return row['id'] if row else None

    def get_signals(self, limit=30, active_only=False):
        clause="WHERE state IN ('TRIGGERED','PAPER_FILLED')" if active_only else ''
        with self.conn() as db:
            rows=db.execute(f'SELECT * FROM signals {clause} ORDER BY id DESC LIMIT ?', (min(max(limit,1),100),)).fetchall()
        return [{**dict(r),'details':json.loads(r['details_json'])} for r in rows]

    def update_signal_state(self, signal_id, new_state, price=None, note=''):
        from .signal_engine import transition
        with self.conn() as db:
            row=db.execute('SELECT state FROM signals WHERE id=?',(signal_id,)).fetchone()
            if not row:raise ValueError('Unknown signal id')
            transition(row['state'],new_state)
            db.execute('UPDATE signals SET state=?,updated_at=? WHERE id=?',(new_state,iso(),signal_id))
            db.execute('INSERT INTO signal_events(signal_id,event_type,occurred_at,price,note) VALUES(?,?,?,?,?)',
                       (signal_id,new_state,iso(),price,note[:400]))

    def save_kronos(self, record):
        with self.conn() as db:
            db.execute("""INSERT INTO kronos_forecasts
                 (symbol,timeframe,cutoff_ts,horizon_bars,model_version,created_at,forecast_json)
                 VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                 (record['symbol'],record['timeframe'],record['cutoff_ts'],record['horizon_bars'],
                  record['model_version'],iso(),json.dumps(record,allow_nan=False)))

    def latest_kronos(self, symbol=None):
        with self.conn() as db:
            if symbol:
                rows=db.execute('SELECT * FROM kronos_forecasts WHERE symbol=? ORDER BY id DESC LIMIT 3',(symbol,)).fetchall()
            else:
                rows=db.execute('SELECT * FROM kronos_forecasts ORDER BY id DESC LIMIT 12').fetchall()
        return [json.loads(r['forecast_json']) for r in rows]


    def paper_statistics(self, symbol=None):
        where='WHERE s.symbol=?' if symbol else ''
        params=(symbol,) if symbol else ()
        with self.conn() as db:
            rows=db.execute(f"""SELECT s.symbol,s.direction,s.state,f.fill_price,f.exit_price,
                f.net_pnl_pct,f.exit_reason FROM signals s LEFT JOIN paper_fills f
                ON f.signal_id=s.id {where} ORDER BY s.id""",params).fetchall()
        closed=[dict(x) for x in rows if x['state']=='CLOSED' and x['net_pnl_pct'] is not None]
        entered=[x for x in rows if x['fill_price'] is not None]
        won=[x for x in closed if x['net_pnl_pct']>0]
        return {'total_signals':len(rows),'paper_filled':len(entered),'paper_closed':len(closed),
                'unfilled':sum(x['state']=='UNFILLED' for x in rows),
                'win_rate_pct':100*len(won)/len(closed) if closed else None,
                'avg_pnl_pct':sum(x['net_pnl_pct'] for x in closed)/len(closed) if closed else None,
                'sample_warning':len(closed)<30,
                'statement':'Paper simulated OHLCV fills only; not exchange executions or live profitability'}
