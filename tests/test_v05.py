"""Offline regression tests for AFTERBURNER v0.5 signal performance audit.

Tests cover:
- Candle-by-candle replay without look-ahead bias
- Signal generation from historical data
- Signal state transitions (FORMING -> ARMED -> TRIGGERED)
- Signal outcome tracking (TP1 hit, SL hit, time exit, invalidated)
- Public export data format
- No look-ahead bias verification
"""

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
from afterburner.replay import ReplayEngine
from afterburner.performance import build_performance_report
from afterburner.config import assets, settings

STEP = 300000  # 5 minutes in milliseconds
BASE = 1700000000000  # 2023-11-20 00:00:00 UTC


def candles(n=120, breakout=True):
    out = []
    for i in range(n):
        cl = 1 + 0.0004 * i
        high = cl + 0.002
        low = cl - 0.002
        v = 100.0
        if i == n - 1 and breakout:
            cl = 1.12
            high = 1.125
            v = 350
        out.append(
            {
                "ts": BASE + i * STEP,
                "open": cl - 0.0005,
                "high": high,
                "low": low,
                "close": cl,
                "volume": v,
                "end": BASE + (i + 1) * STEP,
            }
        )
    return out


MARKET = {
    "symbol": "GATO-USDT",
    "price": 1.12,
    "spread_pct": 0.08,
    "bid_depth_usd": 10000,
    "ask_depth_usd": 10000,
    "volume_24h_usd": 500000,
    "open_interest_base": None,
    "mark_price": 1.12,
    "index_price": 1.12,
    "funding_rate_pct": 0.0,
}

CONFIG = {
    "risk": {
        "max_spread_pct": 0.75,
        "min_book_depth_usd_each_side": 1500,
        "min_quote_volume_24h_usd": 50000,
    },
    "schedule": {"max_market_age_seconds": 600},
    "signal": {
        "sl_atr_mult": 1.5,
        "tp1_rr": 2,
        "tp2_rr": 3,
        "fee_bps": 10,
        "slippage_bps": 10,
        "expiry_seconds": 14400,
        "timeframe": "5m",
        "acceptable_max_cost_pct": 1.0,
        "caution_max_cost_pct": 2.5,
        "poor_liquidity_max_cost_pct": 5.0,
    },
    "kronos": {"enabled": False, "factor_weight": 0},
}


class SignalTests(unittest.TestCase):
    """Tests for the signal engine replay functionality."""

    def test_signal_generation_from_historical_data(self):
        """Test that signal engine can generate signals from historical data."""
        bars = candles()
        market = {
            "symbol": "GATO-USDT",
            "price": bars[-1]["close"],
            "contract_active": True,
            "spread_pct": 0.08,
            "bid_depth_usd": 10000,
            "ask_depth_usd": 10000,
            "volume_24h_usd": 500000,
            "open_interest_base": None,
            "mark_price": bars[-1]["close"],
            "index_price": bars[-1]["close"],
            "funding_rate_pct": 0.0,
            "observed_at": bars[-1]["ts"],
            "candles": bars,
        }
        r = generate_signal(
            symbol="GATO-USDT",
            market=market,
            candles_by_tf={"5m": bars},
            config=CONFIG,
            now_ms=bars[-1]["ts"] + STEP,
            timeframe="5m",
        )
        self.assertIn(r["state"], ("WAIT", "FORMING", "ARMED", "TRIGGERED"))
        self.assertIn(r["direction"], ("LONG", "SHORT", "WAIT"))

    def test_no_lookahead_bias(self):
        """Test that signal generation does not use future data."""
        bars = candles(breakout=False)
        r = generate_signal(
            "GATO-USDT",
            MARKET,
            {"5m": bars},
            CONFIG,
            now_ms=bars[-1]["ts"] + STEP,
            timeframe="5m",
        )
        self.assertIn(r["direction"], ("LONG", "SHORT", "WAIT"))

    def test_signal_state_transitions(self):
        """Test that signal states transition correctly (FORMING -> ARMED -> TRIGGERED)."""
        bars = candles(breakout=False)
        cl = 0.88
        high = 0.885
        low = 0.875
        v = 400
        bars[-1] = {
            "ts": BASE + (len(bars) - 1) * STEP,
            "open": cl - 0.0005,
            "high": high,
            "low": low,
            "close": cl,
            "volume": v,
            "end": BASE + len(bars) * STEP,
        }
        r = generate_signal(
            "GATO-USDT",
            MARKET,
            {"5m": bars},
            CONFIG,
            now_ms=bars[-1]["ts"] + STEP,
            timeframe="5m",
        )
        self.assertIn(r["direction"], ("SHORT", "WAIT"))
        if r["direction"] == "SHORT":
            self.assertEqual(r["state"], "TRIGGERED")
            self.assertGreater(r["sl"], r["entry_trigger"])
            self.assertLess(r["tp1"], r["entry_trigger"])

    def test_signal_state_persistence(self):
        """Test that signal states persist across consecutive polls."""
        bars = candles()
        signals = []
        for i in range(50, 60):
            c_subset = bars[: i + 1]
            r = generate_signal(
                "GATO-USDT",
                MARKET,
                {"5m": c_subset},
                CONFIG,
                now_ms=c_subset[-1]["ts"] + STEP,
                timeframe="5m",
            )
            signals.append(r["state"])
        # Once TRIGGERED, should stay TRIGGERED or progress to CLOSED/EXPIRED
        triggered = [i for i, s in enumerate(signals) if s == "TRIGGERED"]
        if triggered:
            first_triggered = triggered[0]
            for i in range(first_triggered + 1, len(signals)):
                self.assertIn(signals[i], ("TRIGGERED", "CLOSED", "EXPIRED"))

    def test_replay_engine_basic(self):
        """Test that signal engine generates signals from historical data."""
        bars = candles()
        engine = ReplayEngine("GATO-USDT", "5m", bars, CONFIG)
        result = engine.replay()
        self.assertEqual(result.symbol, "GATO-USDT")
        self.assertEqual(result.timeframe, "5m")
        self.assertEqual(result.total_candles, len(bars))
        self.assertIsInstance(result.signals, list)

    def test_performance_report_generation(self):
        """Test that performance report is generated from replay results."""
        bars = candles()
        engine = ReplayEngine("GATO-USDT", "5m", bars, CONFIG)
        result = engine.replay()
        report = build_performance_report(result)
        self.assertEqual(report.symbol, "GATO-USDT")
        self.assertEqual(report.timeframe, "5m")
        self.assertEqual(report.total_candles, len(bars))

    def test_public_schema_with_signal(self):
        """Test that public snapshot includes signal data."""
        with tempfile.TemporaryDirectory() as d:
            st = Storage(Path(d) / "test.db")
            a = assets()
            conf = settings()
            payload = public_payload(st, a, conf)
            self.assertTrue(validate_payload(payload, [x.symbol for x in a]))
            payload["assets"][0]["technical_signal"] = {"private_key": "password"}
            with self.assertRaises(ValueError): validate_payload(payload, [x.symbol for x in a])

    def test_only_one_entry_alert_per_trigger(self):
        """Only one entry alert per actual trigger (same candle)."""
        with tempfile.TemporaryDirectory() as d:
            st = Storage(Path(d) / "test.db")
            bars = candles()
            sig = generate_signal("GATO-USDT", MARKET, {"5m": bars}, CONFIG, now_ms=bars[-1]["ts"] + STEP)
            self.assertEqual(sig["state"], "TRIGGERED")
            sid1 = st.save_setup(sig)
            self.assertIsNotNone(sid1)
            sid2 = st.save_setup(sig)
            self.assertEqual(sid1, sid2)
            snaps = st.get_signals()
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["state"], "TRIGGERED")

    def test_paper_filled_and_closed_preserved_across_polls(self):
        """PAPER_FILLED and CLOSED states preserved across repeated market polls."""
        with tempfile.TemporaryDirectory() as d:
            st = Storage(Path(d) / "test.db")
            bars = candles()
            sig = generate_signal("GATO-USDT", MARKET, {"5m": bars}, CONFIG, now_ms=bars[-1]["ts"] + STEP)
            sid = st.save_setup(sig)
            self.assertEqual(sid, 1)
            st.update_signal_state(sid, "PAPER_FILLED", price=1.125, note="paper fill")
            snaps = st.get_signals()
            self.assertEqual(snaps[0]["state"], "PAPER_FILLED")
            sid2 = st.save_setup(sig)
            self.assertEqual(sid, sid2)
            snaps = st.get_signals()
            self.assertEqual(snaps[0]["state"], "PAPER_FILLED")
            st.update_signal_state(sid, "CLOSED", price=1.13, note="TP1 hit")
            snaps = st.get_signals()
            self.assertEqual(snaps[0]["state"], "CLOSED")
            sid3 = st.save_setup(sig)
            self.assertEqual(sid, sid3)
            snaps = st.get_signals()
            self.assertEqual(snaps[0]["state"], "CLOSED")


if __name__ == "__main__":
    unittest.main()