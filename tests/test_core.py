"""Offline tests: no exchange credentials, no network, no JAFAR dependencies."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from threading import Thread
import requests
import os
from unittest.mock import patch
from afterburner.service import Radar
import json
import tempfile
import unittest
from afterburner.config import assets, settings
from afterburner.bingx import BingX, book_metrics, candles_normalized
from afterburner.dex import parse_pools
from afterburner.news import parse_feed, classify_title
from afterburner.signals import evaluate, momentum_features
from afterburner.storage import Storage
from afterburner.dashboard import build_server


class FakeHTTP:
    def __init__(self, symbol):
        self.symbol = symbol
        self.now = int(datetime.now(timezone.utc).timestamp()*1000)

    def get(self, url, params=None, response_type="json"):
        s = (params or {}).get("symbol") or self.symbol
        if "contracts" in url:
            return {"code": 0, "data": [{"symbol":s,"status":1}]}
        if "quote/ticker" in url:
            return {"code": 0, "data":{"symbol":s,"lastPrice":"0.008", "priceChangePercent":"14", "quoteVolume":"1800000"}}
        if "quote/depth" in url:
            return {"code": 0, "data":{"bids":[["0.00799","1000000"]],"asks":[["0.00801","1000000"]]}}
        if "openInterest" in url:
            return {"code": 0, "data":{"symbol":s,"openInterest":"10000000"}}
        if "premiumIndex" in url:
            return {"code": 0, "data":{"symbol":s,"markPrice":"0.008", "indexPrice":"0.008", "lastFundingRate":"0.0005"}}
        if "klines" in url:
            start = (self.now//300000-100)*300000
            rows = [[start+i*300000, f"{0.005+i*.00003:.8f}", f"{0.0052+i*.00003:.8f}",
                     f"{0.0049+i*.00003:.8f}",f"{0.005+i*.00003:.8f}", str(1000+i*100),
                     start+(i+1)*300000-1] for i in range(99)]
            return {"code": 0, "data": rows[::-1]}
        if "dexscreener" in url:
            return [{"chainId":"solana", "dexId":"pumpswap", "baseToken":{"address": url.rsplit('/',1)[-1],"symbol":"GATO"},
                     "quoteToken":{"symbol":"SOL"}, "liquidity":{"usd":190000}, "priceUsd":"0.008",
                     "volume":{"h24":400000}, "txns":{"h24":{"buys":340,"sells":200}}}]
        raise ValueError(url)


class TestCore(unittest.TestCase):
    def test_config_and_flags(self):
        a = assets()
        self.assertEqual(len(a), 4)
        self.assertFalse(any(x.mint_verified for x in a))
        self.assertEqual(len(set(x.symbol for x in a)), 4)

    def test_book_spread(self):
        r = book_metrics({"bids":[["9.9","100"]],"asks":[["10.1","100"]]})
        self.assertAlmostEqual(r["spread_pct"], 2)
        self.assertAlmostEqual(r["bid_depth_usd"], 990)

    def test_candles_newest_first(self):
        now = int(datetime.now(timezone.utc).timestamp()*1000)
        rows = [[now-500000,1,2,1,1.3,10,now-450000], [now-700000,1,2,1,1.1,10,now-650000]]
        self.assertEqual(candles_normalized(rows)[0]["close"],1.1)

    def test_pair_identity_excludes_wrong_mint(self):
        pools = [{"chainId":"solana", "baseToken":{"address":"MINT_BAD"},
                  "quoteToken":{"symbol":"SOL"}, "liquidity":{"usd":99999999}},
                 {"chainId":"solana", "baseToken":{"address":"MINT_GOOD"},
                  "quoteToken":{"symbol":"SOL"}, "liquidity":{"usd":100000}}]
        d = parse_pools(pools, "MINT_GOOD")
        self.assertEqual(d["matched_pools"], 1)
        self.assertEqual(d["primary"]["liquidity_usd"], 100000)

    def test_feed_and_relevance(self):
        raw = b'<rss><channel><item><title>Rockstar GTA VI trailer announced</title><link>https://example.com/1</link><pubDate>Sat, 14 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>'
        row = parse_feed(raw)[0]
        self.assertEqual(row["title"], "Rockstar GTA VI trailer announced")
        ok, labels, topic = classify_title(row["title"], assets())
        self.assertTrue(ok)
        self.assertEqual(labels, ["gta6_narrative"])
        self.assertEqual(topic, "trailer")
        _, labeled, _ = classify_title("Dacity coin pumps after trailer", assets())
        self.assertIn("DACITY-USDT", labeled)

    def test_futures_collector(self):
        f = FakeHTTP("GATO-USDT")
        market = BingX(f).collect("GATO-USDT")
        self.assertTrue(market["contract_active"])
        self.assertGreater(market["spread_pct"], 0)
        self.assertEqual(market["funding_rate_pct"], .05)
        self.assertEqual(len(market["candles"]), 99)

    def test_direction_not_news_hype(self):
        a = assets()[0]
        market = BingX(FakeHTTP(a.symbol)).collect(a.symbol)
        dex = {"status":"ok","mint_verified":False,"primary":{"liquidity_usd":150000,"price_usd":.008}}
        article = {"asset_symbols":["gta6_narrative"]}
        empty = evaluate(a,market,dex,None,settings(),[])
        news = evaluate(a,market,dex,None,settings(),[article]*100)
        self.assertEqual(empty["long_score"],news["long_score"])
        self.assertEqual(empty["short_score"],news["short_score"])
        self.assertIn("dex_to_futures_identity_unverified", news["blocking_flags"])
        self.assertFalse(news["tradable"])

    def test_spread_gates(self):
        a = replace(assets()[0],mint_verified=True,verification_evidence="Manually confirmed mint on official provider")
        market = BingX(FakeHTTP(a.symbol)).collect(a.symbol)
        market["spread_pct"] = 4.0
        dex = {"status":"ok","mint_verified":True,"primary":{"liquidity_usd":180000,"price_usd":.008,"quote_symbol":"SOL"}}
        assessment = evaluate(a,market,dex,None,settings(),[])
        self.assertIn("missing_or_excessive_perp_spread",assessment["blocking_flags"])
        self.assertNotEqual(assessment["state"],"REVIEW_CANDIDATE")

    def test_radar_two_cycles_no_notifications(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"AFTERBURNER_NO_ALERTS":"1"}):
            store = Storage(Path(temp)/"private.db")
            radar = Radar(http=FakeHTTP("GATO-USDT"), store=store)
            self.assertEqual(radar.poll_market(),4)
            self.assertEqual(radar.poll_market(),4)
            snaps = store.latest([a.symbol for a in assets()])
            self.assertEqual(len(snaps),4)
            self.assertTrue(all(snaps[a.symbol] is not None for a in assets()))
            self.assertTrue(all("candles" not in snaps[a.symbol]["market"] for a in assets()))
            self.assertTrue(all(snaps[a.symbol]["signal"]["tradable"] is False for a in assets()))
            self.assertEqual(len(store.history("GATO-USDT")),2)

    def test_storage_isolated_and_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Storage(Path(temp)/"afterburner.db")
            store.set_health("test","ok","passing")
            self.assertEqual(store.health()[0]["source"],"test")
            self.assertTrue(store.insert_article({"source_id":"unit","headline":"GTA VI trailer",
                                                  "asset_symbols":["gta6_narrative"],"topic":"trailer"}))
            self.assertFalse(store.insert_article({"source_id":"unit","headline":"GTA VI trailer",
                                                   "asset_symbols":[],"topic":"trailer"}))
            self.assertEqual(len(store.articles()), 1)
            conf = settings()
            conf["server"] = {"host":"127.0.0.1","port":0}
            server = build_server(store,assets(),conf)
            self.assertEqual(server.server_address[0],"127.0.0.1")
            thread=Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                root = "http://127.0.0.1:"+str(server.server_address[1])
                self.assertEqual(requests.get(root+"/healthz",timeout=2).json()["execution"],"disabled")
                self.assertEqual(len(requests.get(root+"/api/assets",timeout=2).json()["assets"]),4)
                self.assertEqual(requests.get(root+"/api/history?symbol=BAD",timeout=2).status_code,400)
                self.assertIn("AFTERBURNER",requests.get(root+"/",timeout=2).text)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
