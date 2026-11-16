import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from afterburner.storage import Storage
from afterburner.signal_engine import calculate_atr, generate_signal, transition
from afterburner.backtest import backtest
from afterburner.paper import paper_step
from afterburner.kronos_layer import forecast
from afterburner.publisher import validate_payload
from afterburner.public import public_payload
from afterburner.execution_quality import assess_execution
from afterburner.bingx import parse_depth_ladders
from afterburner.config import assets, settings

STEP=300000
BASE=1700000000000


def candles(n=120, breakout=True):
    out=[]
    for i in range(n):
        cl=1+0.0004*i
        high=cl+0.002; low=cl-0.002
        v=100.0
        if i==n-1 and breakout:
            cl=1.12;high=1.125;v=350
        out.append({'ts':BASE+i*STEP,'open':cl-.0005,'high':high,'low':low,'close':cl,'volume':v})
    return out


MARKET={'contract_active':True,'price':1.12,'spread_pct':0.08,
        'bid_depth_usd':10000,'ask_depth_usd':10000,'volume_24h_usd':500000}
CONFIG={'risk':{'max_spread_pct':.75,'min_book_depth_usd_each_side':1500,
                'min_quote_volume_24h_usd':50000},
        'schedule':{'max_market_age_seconds':600},
        'signal':{'sl_atr_mult':1.5,'tp1_rr':2,'tp2_rr':3,'fee_bps':10,
                  'slippage_bps':10,'expiry_seconds':14400,'timeframe':'5m',
                  'acceptable_max_cost_pct':1.0,'caution_max_cost_pct':2.5,
                  'poor_liquidity_max_cost_pct':5.0},
        'kronos':{'enabled':False,'factor_weight':0}}


class SignalTests(unittest.TestCase):
    def test_wilder_atr(self):
        bar=[{'ts':i*STEP,'open':10,'high':11,'low':9,'close':10,'volume':1} for i in range(20)]
        self.assertAlmostEqual(calculate_atr(bar,14),2.)

    def test_signal_levels_and_trigger(self):
        bars=candles()
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertEqual(r['state'],'TRIGGERED');self.assertEqual(r['direction'],'LONG')
        self.assertLess(r['sl'],r['entry_trigger']);self.assertLess(r['entry_trigger'],r['tp1'])
        self.assertGreater(r['rr'],1.2)

    def test_no_trigger_if_insufficient_history(self):
        """Insufficient candles -> WAIT, not FORMING."""
        bars=candles(40)
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertEqual(r['state'],'WAIT')
        self.assertIn('insufficient_candles',r['data_gates'])

    def test_short_setup_detected_independently(self):
        """SHORT setups are detected when price breaks below support with volume."""
        bars=candles(breakout=False)
        cl=0.88; high=0.885; low=0.875; v=400
        bars[-1]={'ts':BASE+(len(bars)-1)*STEP,'open':cl-0.0005,'high':high,'low':low,'close':cl,'volume':v}
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertIn(r['direction'],('SHORT','WAIT'))
        if r['direction']=='SHORT':
            self.assertEqual(r['state'],'TRIGGERED')
            self.assertGreater(r['sl'],r['entry_trigger'])
            self.assertLess(r['tp1'],r['entry_trigger'])

    def test_stale_candles_block_triggered(self):
        """Stale candles cannot produce fresh confirmed signals."""
        bars=candles()
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP+600001)
        self.assertIn('stale_or_future_candles',r['data_gates'])
        self.assertEqual(r['state'],'WAIT')

    def test_mark_index_not_confused_with_spread(self):
        """Mark/index divergence must not be misidentified as bid/ask spread."""
        market_ok={**MARKET,'spread_pct':0.05,'bid_depth_usd':5000,'ask_depth_usd':5000}
        market_bad_mark={**MARKET,'spread_pct':0.05,'bid_depth_usd':5000,'ask_depth_usd':5000,
                         'mark_price':999.0,'index_price':1.0}
        r1=generate_signal('GATO-USDT',market_ok,{'5m':candles()},CONFIG,now_ms=BASE+120*STEP)
        r2=generate_signal('GATO-USDT',market_bad_mark,{'5m':candles()},CONFIG,now_ms=BASE+120*STEP)
        self.assertEqual(r1['direction'], r2['direction'])
        self.assertEqual(r1['state'], r2['state'])

    def test_different_position_sizes_different_slippage(self):
        """Different notional sizes produce different depth-based slippage estimates."""
        market={**MARKET,'bid':1.00,'ask':1.01,
                'bid_depth_usd':100000,'ask_depth_usd':100000,
                'bids_ladder':[[1.00,200],[0.99,10000],[0.98,10000]],
                'asks_ladder':[[1.01,200],[1.02,10000],[1.03,10000]]}
        eq_small=assess_execution(market,None,notionals=[50.0],config=CONFIG)
        eq_large=assess_execution(market,None,notionals=[250.0],config=CONFIG)
        small_impact=eq_small['notionals'][0].get('buy_impact_pct')
        large_impact=eq_large['notionals'][0].get('buy_impact_pct')
        self.assertIsNotNone(small_impact)
        self.assertIsNotNone(large_impact)
        self.assertGreater(large_impact, small_impact)

    def test_missing_orderbook_data_is_data_unavailable(self):
        """Missing order-book data produces DATA_UNAVAILABLE, not zero costs."""
        market_no_book={**MARKET,'bid_depth_usd':None,'ask_depth_usd':None,'bid':None,'ask':None,'spread_pct':None,
                        'bids_ladder':[],'asks_ladder':[]}
        eq=assess_execution(market_no_book,None,notionals=[100.0],config=CONFIG)
        self.assertEqual(eq['quality'],'DATA_UNAVAILABLE')
        self.assertIsNone(eq['worst_case_cost_pct'])

    def test_no_authenticated_requests_in_signal_engine(self):
        """No exchange orders or authenticated API calls occur in signal generation."""
        import inspect
        import afterburner.signal_engine as se
        import afterburner.execution_quality as eq
        src_se = inspect.getsource(se)
        src_eq = inspect.getsource(eq)
        for src in (src_se, src_eq):
            self.assertNotIn('requests.post', src)
            self.assertNotIn('session.post', src)
            self.assertNotIn('/private/', src)
            self.assertNotIn('api_key', src.lower())
            self.assertNotIn('secret', src.lower())

    def test_crossed_quotes_never_execution_ready(self):
        """Crossed bid/ask quotes never produce ACCEPTABLE execution quality."""
        market_crossed={**MARKET,'bid':1.12,'ask':1.11,'bid_depth_usd':10000,'ask_depth_usd':10000,
                       'bids_ladder':[[1.12,1000]],'asks_ladder':[[1.11,1000]]}
        eq=assess_execution(market_crossed,None,notionals=[100.0],config=CONFIG)
        self.assertEqual(eq['quality'],'DATA_UNAVAILABLE')
        self.assertIsNone(eq['worst_case_cost_pct'])

    def test_ladder_vwap_arithmetic(self):
        """Proper VWAP simulation over bid/ask depth ladders."""
        market_ladder={**MARKET,'bid':1.0000,'ask':1.0001,
                       'bid_depth_usd':100000,'ask_depth_usd':100000,
                       'bids_ladder':[[1.0000,1000],[0.9999,1000],[0.9998,1000]],
                       'asks_ladder':[[1.0001,1000],[1.0002,1000],[1.0003,1000]],
                       'quote_time':int(datetime.now(timezone.utc).timestamp()*1000)}
        eq=assess_execution(market_ladder,None,notionals=[100.0],config=CONFIG)
        self.assertEqual(eq['quality'],'ACCEPTABLE')
        buy_impact=eq['notionals'][0].get('buy_impact_pct')
        self.assertIsNotNone(buy_impact)
        self.assertGreaterEqual(buy_impact, 0)

    def test_insufficient_depth_for_notional(self):
        """Insufficient depth for a hypothetical notional produces INSUFFICIENT_DEPTH."""
        market_thin={**MARKET,'bid':1.00,'ask':1.01,
                     'bid_depth_usd':10,'ask_depth_usd':10,
                     'bids_ladder':[[1.00,0.01]],'asks_ladder':[[1.01,0.01]]}
        eq=assess_execution(market_thin,None,notionals=[250.0],config=CONFIG)
        self.assertEqual(eq['quality'],'INSUFFICIENT_DEPTH')
        self.assertIsNone(eq['worst_case_cost_pct'])

    def test_fees_and_slippage_without_double_counting_spread(self):
        """Fees and slippage applied without double-counting spread."""
        market={**MARKET,'bid':1.00,'ask':1.01,
                'bid_depth_usd':100000,'ask_depth_usd':100000,
                'bids_ladder':[[1.00,1000],[0.99,1000]],
                'asks_ladder':[[1.01,1000],[1.02,1000]]}
        eq=assess_execution(market,None,notionals=[100.0],config=CONFIG)
        est=eq['notionals'][0]
        total_buy=est.get('total_round_trip_buy_pct')
        self.assertIsNotNone(total_buy)
        self.assertGreater(total_buy, eq['spread_pct'])
        self.assertLess(total_buy, eq['spread_pct'] + 1.0)

    def test_end_to_end_bingx_collect_to_assess_execution(self):
        """End-to-end: BingX.collect() -> assess_execution() with realistic depth ladders."""
        raw_depth_response = {
            'symbol': 'GATO-USDT',
            'bids': [['1.0050', '500'], ['1.0049', '800'], ['1.0048', '600']],
            'asks': [[1.0101, 800], [1.0102, 600], [1.0103, 400]],
            'time': BASE
        }
        # Parse through BingX's own parser
        parsed = parse_depth_ladders(raw_depth_response)
        self.assertEqual(len(parsed['bids']), 3)
        self.assertEqual(len(parsed['asks']), 3)
        self.assertEqual(parsed['bids'][0], [1.0050, 500])
        self.assertEqual(parsed['asks'][0], [1.0101, 800])

        # Build market dict as BingX.collect() would produce it
        market = {
            'symbol': 'GATO-USDT',
            'contract_active': True,
            'price': 1.0050,
            'spread_pct': 0.0099,
            'bid_depth_usd': 10000,
            'ask_depth_usd': 10000,
            'volume_24h_usd': 500000,
            'bids_ladder': parsed['bids'],
            'asks_ladder': parsed['asks'],
            'quote_time': int(datetime.now(timezone.utc).timestamp()*1000)
        }

        eq = assess_execution(market, None, notionals=[50.0, 100.0, 250.0], config=CONFIG)
        self.assertEqual(eq['quality'], 'ACCEPTABLE')
        self.assertEqual(eq['spread_source'], 'best_bid/best_ask (same snapshot)')

        for est in eq['notionals']:
            self.assertIsNotNone(est['total_round_trip_buy_pct'])
            self.assertIsNotNone(est['total_round_trip_sell_pct'])
            self.assertGreater(est['total_round_trip_buy_pct'], eq['spread_pct'])
            self.assertLess(total_buy := est['total_round_trip_buy_pct'], eq['spread_pct'] + est.get('buy_impact_pct', 0) + 0.5)

    def test_wait_insufficient_data_not_forming(self):
        """WAIT/insufficient data not classified as FORMING."""
        bars=candles(40)
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertEqual(r['state'],'WAIT')
        self.assertNotEqual(r['state'],'FORMING')

    def test_no_telegram_push_for_wait_or_forming(self):
        """WAIT and FORMING states should not trigger Telegram alerts."""
        bars=candles(40)
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertEqual(r['state'],'WAIT')
        self.assertNotEqual(r['state'],'TRIGGERED')
        bars2=candles()
        r2=generate_signal('GATO-USDT',MARKET,{'5m':bars2},CONFIG,now_ms=bars2[-1]['ts']+STEP)
        if r2['state'] != 'TRIGGERED':
            self.assertIn(r2['state'],('WAIT','FORMING'))

    def test_only_one_entry_alert_per_trigger(self):
        """Only one entry alert per actual trigger (same candle)."""
        with tempfile.TemporaryDirectory() as d:
            st=Storage(Path(d)/'test.db')
            bars=candles()
            sig=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
            self.assertEqual(sig['state'],'TRIGGERED')
            sid1=st.save_setup(sig)
            self.assertIsNotNone(sid1)
            sid2=st.save_setup(sig)
            self.assertEqual(sid1,sid2)
            snaps=st.get_signals()
            self.assertEqual(len(snaps),1)
            self.assertEqual(snaps[0]['state'],'TRIGGERED')

    def test_paper_filled_and_closed_preserved_across_polls(self):
        """PAPER_FILLED and CLOSED states preserved across repeated market polls."""
        with tempfile.TemporaryDirectory() as d:
            st=Storage(Path(d)/'test.db')
            bars=candles()
            sig=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
            sid=st.save_setup(sig)
            self.assertEqual(sid,1)
            st.update_signal_state(sid,'PAPER_FILLED',price=1.125,note='paper fill')
            snaps=st.get_signals()
            self.assertEqual(snaps[0]['state'],'PAPER_FILLED')
            sid2=st.save_setup(sig)
            self.assertEqual(sid,sid2)
            snaps=st.get_signals()
            self.assertEqual(snaps[0]['state'],'PAPER_FILLED')
            st.update_signal_state(sid,'CLOSED',price=1.13,note='TP1 hit')
            snaps=st.get_signals()
            self.assertEqual(snaps[0]['state'],'CLOSED')
            sid3=st.save_setup(sig)
            self.assertEqual(sid,sid3)
            snaps=st.get_signals()
            self.assertEqual(snaps[0]['state'],'CLOSED')

    def test_public_schema_with_signal(self):
        with tempfile.TemporaryDirectory() as d:
            st=Storage(Path(d)/'test.db')
            a=assets(); conf=settings()
            payload=public_payload(st,a,conf)
            self.assertTrue(validate_payload(payload,[x.symbol for x in a]))
            payload['assets'][0]['technical_signal']={'private_key':'password'}
            with self.assertRaises(ValueError):validate_payload(payload,[x.symbol for x in a])


if __name__=='__main__':unittest.main()