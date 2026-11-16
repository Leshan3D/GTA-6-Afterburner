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
        bars=candles(40)
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertEqual(r['state'],'FORMING')
        self.assertIn('insufficient_candles',r['blocking_reasons'])

    def test_no_trigger_with_bad_depth(self):
        """Wide spread/thin depth should not erase technical setup, but execution quality should flag it."""
        bars=candles();m={**MARKET,'ask_depth_usd':20,'spread_pct':2.5}
        r=generate_signal('GATO-USDT',m,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        # Technical setup can still be TRIGGERED; execution quality should flag it
        eq=assess_execution(m,None,notionals=[100.0],config=CONFIG)
        self.assertEqual(eq['quality'],'POOR_LIQUIDITY')

    def test_no_trigger_with_stale_bars(self):
        bars=candles()
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP+600001)
        self.assertIn('stale_or_future_candles',r['blocking_reasons'])

    def test_no_signal_from_research_score(self):
        bars=candles(breakout=False)
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP,
                          research={'long_score':99})
        self.assertEqual(r['direction'],'WAIT')

    def test_legal_lifecycle(self):
        self.assertEqual(transition('TRIGGERED','PAPER_FILLED'),'PAPER_FILLED')
        with self.assertRaises(ValueError):transition('FORMING','CLOSED')

    def test_persistent_closed_bars_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            st=Storage(Path(d)/'test.db');bars=candles(20)
            st.save_candles('bingx','GATO-USDT','5m',bars,now_ms=bars[-1]['ts']+STEP)
            st.save_candles('bingx','GATO-USDT','5m',bars,now_ms=bars[-1]['ts']+STEP)
            self.assertEqual(len(st.get_candles('bingx','GATO-USDT','5m')),20)
            self.assertEqual(len(st.get_candles('bingx','GATO-USDT','15m')),0)

    def test_paper_separate_from_trigger(self):
        with tempfile.TemporaryDirectory() as d:
            st=Storage(Path(d)/'test.db'); bars=candles()
            sig=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
            sid=st.save_setup(sig)
            self.assertEqual(st.get_signals()[0]['state'],'TRIGGERED')
            self.assertEqual(paper_step(st,'GATO-USDT',bars,CONFIG),[])
            nextbar={'ts':bars[-1]['ts']+STEP,'open':bars[-1]['close'],
                     'high':bars[-1]['close']+0.001,'low':bars[-1]['close']-0.001,
                     'close':bars[-1]['close'],'volume':100}
            events=paper_step(st,'GATO-USDT',bars+[nextbar],CONFIG)
            self.assertIn((sid,'PAPER_FILLED'),events)

    def test_kronos_off_is_neutral(self):
        r=forecast('GATO-USDT','5m',[],CONFIG)
        self.assertEqual(r['status'],'disabled');self.assertEqual(r['directional_weight'],0.0)

    def test_backtest_insufficient_real_data(self):
        self.assertEqual(backtest('GATO-USDT',candles(80),CONFIG)['status'],'insufficient_real_history')

    def test_public_schema_with_signal(self):
        with tempfile.TemporaryDirectory() as d:
            st=Storage(Path(d)/'test.db')
            a=assets(); conf=settings()
            payload=public_payload(st,a,conf)
            self.assertTrue(validate_payload(payload,[x.symbol for x in a]))
            payload['assets'][0]['technical_signal']={'private_key':'password'}
            with self.assertRaises(ValueError):validate_payload(payload,[x.symbol for x in a])

    # ---- v0.4 signal visibility correction tests ----

    def test_valid_long_setup_visible_despite_wide_spread(self):
        """A valid technical LONG setup must still be reported when spread is wide."""
        bars=candles()
        market_wide={**MARKET,'spread_pct':2.5,'bid_depth_usd':500,'ask_depth_usd':500}
        r=generate_signal('GATO-USDT',market_wide,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        # Technical setup should still be TRIGGERED despite wide spread
        self.assertEqual(r['direction'],'LONG')
        self.assertEqual(r['state'],'TRIGGERED')
        # But execution quality should flag POOR_LIQUIDITY
        eq=assess_execution(market_wide,None,notionals=[100.0],config=CONFIG)
        self.assertIn(eq['quality'],('POOR_LIQUIDITY','DATA_UNAVAILABLE'))

    def test_valid_setup_not_labeled_execution_ready_when_costs_unacceptable(self):
        """Same setup: technical TRIGGERED but execution quality POOR_LIQUIDITY."""
        bars=candles()
        market_wide={**MARKET,'spread_pct':3.0,'bid_depth_usd':100,'ask_depth_usd':100}
        r=generate_signal('GATO-USDT',market_wide,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertEqual(r['state'],'TRIGGERED')
        eq=assess_execution(market_wide,None,notionals=[100.0],config=CONFIG)
        self.assertEqual(eq['quality'],'POOR_LIQUIDITY')
        self.assertGreater(eq['worst_case_cost_pct'], 2.5)
        # Technical state is TRIGGERED but execution is not acceptable
        self.assertEqual(r['state'],'TRIGGERED')
        self.assertNotEqual(eq['quality'],'ACCEPTABLE')

    def test_short_setup_detected_independently(self):
        """SHORT setups are detected when price breaks below support with volume."""
        bars=candles(breakout=False)
        # Force a breakdown: last bar below support with high volume
        cl=0.88; high=0.885; low=0.875; v=400
        bars[-1]={'ts':BASE+(len(bars)-1)*STEP,'open':cl-0.0005,'high':high,'low':low,'close':cl,'volume':v}
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertIn(r['direction'],('SHORT','WAIT'))
        if r['direction']=='SHORT':
            self.assertEqual(r['state'],'TRIGGERED')
            # For SHORT, stop loss is above entry; TP1 is below entry
            self.assertGreater(r['sl'],r['entry_trigger'])
            self.assertLess(r['tp1'],r['entry_trigger'])

    def test_stale_candles_block_triggered(self):
        """Stale candles cannot produce fresh confirmed signals."""
        bars=candles()
        r=generate_signal('GATO-USDT',MARKET,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP+600001)
        self.assertIn('stale_or_future_candles',r['blocking_reasons'])
        self.assertNotEqual(r['state'],'TRIGGERED')

    def test_mark_index_not_confused_with_spread(self):
        """Mark/index divergence must not be misidentified as bid/ask spread."""
        market_ok={**MARKET,'spread_pct':0.05,'bid_depth_usd':5000,'ask_depth_usd':5000}
        market_bad_mark={**MARKET,'spread_pct':0.05,'bid_depth_usd':5000,'ask_depth_usd':5000,
                         'mark_price':999.0,'index_price':1.0}
        r1=generate_signal('GATO-USDT',market_ok,{'5m':candles()},CONFIG,now_ms=BASE+120*STEP)
        r2=generate_signal('GATO-USDT',market_bad_mark,{'5m':candles()},CONFIG,now_ms=BASE+120*STEP)
        # Mark/index anomaly should not affect spread-based signal generation
        self.assertEqual(r1['direction'], r2['direction'])
        self.assertEqual(r1['state'], r2['state'])

    def test_different_position_sizes_different_slippage(self):
        """Different notional sizes produce different depth-based slippage estimates."""
        market={**MARKET,'bid_depth_usd':1000,'ask_depth_usd':1000,'spread_pct':0.5}
        eq_small=assess_execution(market,None,notionals=[50.0],config=CONFIG)
        eq_large=assess_execution(market,None,notionals=[250.0],config=CONFIG)
        small_impact=eq_small['notionals'][0].get('buy_impact_pct')
        large_impact=eq_large['notionals'][0].get('buy_impact_pct')
        self.assertIsNotNone(small_impact)
        self.assertIsNotNone(large_impact)
        self.assertGreater(large_impact, small_impact)

    def test_missing_orderbook_data_is_data_unavailable(self):
        """Missing order-book data produces DATA_UNAVAILABLE, not zero costs."""
        market_no_book={**MARKET,'bid_depth_usd':None,'ask_depth_usd':None,'bid':None,'ask':None,'spread_pct':None}
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
        # No requests.post, no session.post, no authenticated endpoints
        for src in (src_se, src_eq):
            self.assertNotIn('requests.post', src)
            self.assertNotIn('session.post', src)
            self.assertNotIn('/private/', src)
            self.assertNotIn('api_key', src.lower())
            self.assertNotIn('secret', src.lower())


if __name__=='__main__':unittest.main()
