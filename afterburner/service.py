"""Single market writer; separate news cadence, Telegram and local Flask server."""
from __future__ import annotations
import logging
import signal
import threading
import time

from .alerts import Telegram
from .bingx import BingX
from .config import assets as read_assets, settings, sources, data_dir
from .dashboard import build_server
from .dex import DexScreener
from .net import HTTP
from .news import News
from .signals import evaluate
from .reporting import status_report
from .public import build_public_server
from .storage import Storage, iso
from .signal_engine import generate_signal, TIMEFRAMES
from .kronos_layer import forecast as kronos_forecast
from .backtest import backtest
from .paper import paper_step
from concurrent.futures import ThreadPoolExecutor

LOG = logging.getLogger(__name__)


class Radar:
    def __init__(self, http=None, store=None):
        self.config = settings()
        self.assets = read_assets()
        self.storage = store or Storage(data_dir() / "afterburner.db")
        sched = self.config["schedule"]
        self.http = http or HTTP(timeout=sched["request_timeout_seconds"],
                                 retries=sched["retries"], pace=sched["request_pace_seconds"])
        self.bingx = BingX(self.http)
        self.dex = DexScreener(self.http)
        self.news = News(self.http, self.storage)
        self.telegram = Telegram(self.storage)
        self.stop_event = threading.Event()
        # Single background worker: CPU forecasts never block price collection.
        self.forecast_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='afterburner-kronos')
        self.forecast_future = None

    def poll_news(self):
        counts = self.news.update(sources(), self.assets)
        LOG.info("News checked: %s", counts)
        return counts

    def poll_market(self):
        recent = self.storage.articles(hours=self.config["narrative"]["lookback_hours"])
        count = 0
        for asset in self.assets:
            try:
                market = self.bingx.collect(asset.symbol)
                dex = self.dex.collect(asset)
                old = self.storage.recent_snapshots(asset.symbol, 1)
                sig = evaluate(asset, market, dex, old[0] if old else None, self.config, recent)
                if old is None or len(old) < self.config["risk"].get("min_signal_history_points", 2)-1:
                    sig["state"] = "MONITOR"
                    sig["advisories"].append("warming_up_no_alert")
                # Incremental 5-minute history on every run; larger timeframes
                # only when the last stored completed candle is aging out.
                candle_sets = {}
                for tf in ('5m', '15m', '1h'):
                    try:
                        previous=self.storage.get_candles('bingx',asset.symbol,tf,1)
                        now_ms=int(time.time()*1000)
                        needs_backfill=(not previous or now_ms-previous[-1]['ts'] >= 2*TIMEFRAMES[tf]*1000)
                        if tf=='5m':
                            rows=self.bingx.collect_candles(asset.symbol,tf,500 if not previous else 100)
                        elif needs_backfill:
                            rows=self.bingx.collect_candles(asset.symbol,tf,500 if not previous else 100)
                        else:rows=[]
                        if rows:self.storage.save_candles('bingx',asset.symbol,tf,rows,now_ms=now_ms)
                        candle_sets[tf]=self.storage.get_candles('bingx',asset.symbol,tf,500)
                        self.storage.set_health('ohlcv:'+asset.symbol+':'+tf,'ok' if candle_sets[tf] else 'partial',
                                                f'{len(candle_sets[tf])} stored candles')
                    except Exception as history_error:
                        LOG.warning('OHLCV %s %s unavailable: %s',asset.symbol,tf,type(history_error).__name__)
                        self.storage.set_health('ohlcv:'+asset.symbol+':'+tf,'partial','Candle collection failed')
                        candle_sets[tf]=self.storage.get_candles('bingx',asset.symbol,tf,500)
                # Evaluate previously triggered paper signals using bars that
                # arrived strictly after their trigger (no retrospective fills).
                paper_step(self.storage,asset.symbol,candle_sets['5m'],self.config)
                technical=generate_signal(asset.symbol,market,candle_sets,self.config,research=sig)
                # The existing research score remains separate; DEX identity is
                # never implicitly upgraded by a technical futures-only trigger.
                sig['technical_setup']=technical
                signal_id=self.storage.save_setup(technical)
                if signal_id is not None:
                    self.telegram.technical_signal(technical,signal_id)
                self.storage.save_snapshot(asset.symbol, market, dex, sig)
                self.storage.set_health("bingx:"+asset.symbol,
                                        "ok" if all(v == "ok" for v in market["source_health"].values()) else "partial",
                                        "; ".join(k for k,v in market["source_health"].items() if v != "ok")[:300])
                self.storage.set_health("dex:"+asset.symbol,
                                        "ok" if dex.get("status") == "ok" else "partial", dex.get("status", "unknown"))
                if sig["state"] != "MONITOR":
                    self.telegram.signal(asset, {"market":market,"dex":dex,"signal":sig},
                                         self.config["schedule"]["alert_cooldown_seconds"])
                count += 1
                LOG.info("%s price=%s long=%s short=%s status=%s flags=%s",
                         asset.symbol, market.get("price"), sig["long_score"], sig["short_score"],
                         sig["state"], ",".join(sig["blocking_flags"][:3]))
            except Exception as exc:
                LOG.exception("Asset %s cycle failed", asset.symbol)
                self.storage.set_health("bingx:"+asset.symbol, "error", str(exc))
        self.storage.put_kv("market_last_run", iso())
        return count

    def run_kronos(self):
        for asset in self.assets:
            for tf in ('5m','15m','1h'):
                try:
                    bars=self.storage.get_candles('bingx',asset.symbol,tf,500)
                    previous=self.storage.latest_kronos(asset.symbol)
                    if bars and any(f.get('timeframe')==tf and f.get('cutoff_ts')==bars[-1]['ts']
                                    for f in previous):
                        continue
                    result=kronos_forecast(asset.symbol,tf,bars,self.config)
                    if result.get('status')=='ok':
                        self.storage.save_kronos(result)
                    else:
                        LOG.info('Kronos %s %s: %s',asset.symbol,tf,result.get('status'))
                except Exception:
                    LOG.exception('Kronos failed %s %s',asset.symbol,tf)

    def status_text(self, command="/meme"):
        return status_report(self.assets, self.storage, self.config, command)

    def run_forever(self):
        conf = self.config
        host, port = conf["server"]["host"], int(conf["server"]["port"])
        server = build_server(self.storage, self.assets, conf)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="afterburner-dashboard")
        server_thread.start()
        LOG.info("Dashboard http://%s:%d/", host, port)

        def on_shutdown(*_):
            self.stop_event.set()
        signal.signal(signal.SIGTERM, on_shutdown)
        signal.signal(signal.SIGINT, on_shutdown)
        self.storage.put_kv("service_started_at", iso())
        bot_thread = threading.Thread(target=self.telegram.poll_forever,
                                      args=(self.status_text, self.stop_event),
                                      daemon=True, name="afterburner-telegram")
        bot_thread.start()

        public_server = None
        if conf.get("public", {}).get("enabled", False):
            public_server = build_public_server(self.storage, self.assets, conf)
            public_thread = threading.Thread(target=public_server.serve_forever, daemon=True,
                                             name="afterburner-public-preview")
            public_thread.start()
            LOG.info("Sanitized public preview on http://127.0.0.1:%s/", conf["public"]["port"])
        next_market = next_news = next_kronos = 0
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_news:
                    try:
                        self.poll_news()
                    except Exception:
                        LOG.exception("News run failed")
                    next_news = time.monotonic() + conf["schedule"]["news_seconds"]
                if time.monotonic() >= next_market:
                    self.poll_market()
                    next_market = time.monotonic() + conf["schedule"]["market_seconds"]
                # Forecasts run separately; never stall feed polling.
                if conf.get('kronos',{}).get('enabled') and time.monotonic() >= next_kronos:
                    if self.forecast_future is None or self.forecast_future.done():
                        self.forecast_future=self.forecast_executor.submit(self.run_kronos)
                    next_kronos=time.monotonic()+900
                self.stop_event.wait(2)
        finally:
            self.stop_event.set()
            if public_server is not None:
                public_server.shutdown()
            server.shutdown()
            self.forecast_executor.shutdown(wait=False,cancel_futures=True)
            LOG.info("AFTERBURNER stopped cleanly")
