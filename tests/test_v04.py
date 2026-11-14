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
                  'slippage_bps':10,'expiry_seconds':14400,'timeframe':'5m'},
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
        bars=candles();m={**MARKET,'ask_depth_usd':20}
        r=generate_signal('GATO-USDT',m,{'5m':bars},CONFIG,now_ms=bars[-1]['ts']+STEP)
        self.assertNotEqual(r['state'],'TRIGGERED')

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


if __name__=='__main__':unittest.main()
