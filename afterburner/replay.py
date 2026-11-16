"""Candle-by-candle replay engine for AFTERBURNER v0.5 signal performance audit.

Replays historical OHLCV candles through the AFTERBURNER signal engine
without look-ahead bias. At each candle close, only data available up to
that point is used to generate signals. Recorded signals and their
outcomes (TP/SL hit, expiry, invalidation) are recorded for performance
analysis.

No trading execution occurs - this is purely observational replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from afterburner.config import settings
from afterburner.signal_engine import generate_signal


@dataclass
class SignalEvent:
    """A single signal event detected during replay."""

    symbol: str
    direction: str  # "LONG" or "SHORT"
    state: str  # "FORMING", "ARMED", "TRIGGERED"
    timestamp: int  # timestamp of the candle that triggered this state
    price: float  # price at the time of the signal
    long_score: int
    short_score: int
    confidence: int
    features: dict
    blocking_reasons: list[str]
    advisories: list[str]

    # Outcome fields (filled in by outcome tracker)
    outcome: str | None = None  # "TRIGGERED", "TP1_HIT", "SL_HIT", "TIME_EXIT", "INVALIDATED", "OPEN"
    exit_price: float | None = None
    exit_timestamp: int | None = None
    net_pnl_pct: float | None = None


@dataclass
class ReplayResult:
    """Complete replay result for a symbol/timeframe."""

    symbol: str
    timeframe: str
    total_candles: int
    signals: list[SignalEvent]
    triggered_signals: list[SignalEvent]
    total_signals: int
    triggered_count: int
    tp1_hits: int
    sl_hits: int
    time_exits: int
    invalidated: int
    open_signals: int

    @property
    def win_rate(self) -> float | None:
        """Win rate = TP1 hits / (TP1 hits + SL hits), excluding time exits."""
        decided = self.tp1_hits + self.sl_hits
        if decided == 0:
            return None
        return self.tp1_hits / decided

    @property
    def total_return_pct(self) -> float | None:
        """Sum of net PnL across all closed trades."""
        if not self.signals:
            return None
        returns = [
            s.net_pnl_pct for s in self.signals
            if s.net_pnl_pct is not None
        ]
        if not returns:
            return None
        return sum(returns)


class ReplayEngine:
    """Replays historical candles through the AFTERBURNER signal engine.

    Processes candles one at a time in chronological order. At each candle
    close, only data available up to that point is used to generate signals.
    No look-ahead bias is possible because future candles are never included
    in the input to generate_signal().
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        candles: list[dict],
        config: dict | None = None,
    ):
        """Initialize the replay engine.

        Args:
            symbol: BingX perpetual symbol (e.g., "GATO-USDT")
            timeframe: Timeframe string ("5m", "15m", "1h")
            candles: List of candle dicts sorted by timestamp ascending
            config: AFTERBURNER configuration dict (uses settings() if None)
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.candles = sorted(candles, key=lambda c: c["ts"])
        self.config = config if config is not None else settings()
        self.events: list[SignalEvent] = []

    def replay(self) -> ReplayResult:
        """Run the replay and return results.

        Processes each candle in chronological order. At each candle close,
        only data available up to that point is used to generate signals.
        No look-ahead bias is possible because future candles are never
        included in the input to generate_signal().

        Returns:
            ReplayResult with all detected signals and outcomes.
        """
        signals: list[SignalEvent] = []

        for i, candle in enumerate(self.candles):
            # Build market dict from current candle
            market = self._build_market(i)

            # Generate signal using all candles up to and including current
            sig = generate_signal(
                symbol=self.symbol,
                market=market,
                candles_by_tf={self.timeframe: self.candles[: i + 1]},
                config=self.config,
                now_ms=int(candle["end"]),
                timeframe=self.timeframe,
            )

            # Record signal event if state changed or is TRIGGERED
            sig_event = self._process_signal(sig, i)
            if sig_event is not None:
                signals.append(sig_event)

        # Evaluate outcomes for triggered signals
        self._evaluate_outcomes(signals)

        return self._build_result(signals)

    def _build_market(self, idx: int) -> dict:
        """Build market dict from candle at index idx.

        Only data available at or before this candle is included.
        """
        c = self.candles[idx]
        return {
            "symbol": self.symbol,
            "price": c["close"],
            "contract_active": True,
            "spread_pct": 0.05,
            "bid_depth_usd": 10000.0,
            "ask_depth_usd": 10000.0,
            "volume_24h_usd": 500000.0,
            "open_interest_base": None,
            "mark_price": c["close"],
            "index_price": c["close"],
            "funding_rate_pct": 0.0,
            "observed_at": c["ts"],
            "candles": self.candles[: idx + 1],
        }

    def _process_signal(self, sig: dict, idx: int) -> SignalEvent | None:
        """Process a signal from generate_signal.

        Returns a SignalEvent if the signal state changed or is TRIGGERED.
        Returns None if no change from previous state.
        """
        state = sig.get("state", "UNKNOWN")
        direction = sig.get("direction", "WAIT")

        # Only create event on state change or TRIGGERED
        if self.events and self.events[-1].state == state:
            return None

        return SignalEvent(
            symbol=self.symbol,
            direction=direction,
            state=state,
            timestamp=sig.get("as_of_candle_ts", self.candles[-1]["ts"]),
            price=sig.get("entry_trigger", self.candles[-1]["close"]),
            long_score=sig.get("long_score", 0),
            short_score=sig.get("short_score", 0),
            confidence=sig.get("confidence", 0),
            features=sig.get("features", {}),
            blocking_reasons=sig.get("blocking_reasons", []),
            advisories=sig.get("advisories", []),
        )

    def _evaluate_outcomes(self, signals: list[SignalEvent]) -> None:
        """Evaluate outcomes for triggered signals using subsequent candles.

        For each TRIGGERED signal, scan forward through subsequent candles
        to determine if TP1, SL, or time exit was hit.
        """
        for sig in signals:
            if sig.state != "TRIGGERED":
                continue

            # Find the index of the trigger candle
            trigger_idx = None
            for i, c in enumerate(self.candles):
                if c["ts"] == sig.timestamp:
                    trigger_idx = i
                    break

            if trigger_idx is None:
                continue

            # Extract trade parameters from signal features
            features = sig.features
            sl = features.get("sl")
            tp1 = sig.features.get("tp1") if hasattr(sig, "features") else None

            # Actually, sl/tp1 are in the signal dict directly, not in features
            sl = sig.sl if hasattr(sig, "sl") else None
            tp1 = sig.tp1 if hasattr(sig, "tp1") else None
            direction = sig.direction

            if sl is None or tp1 is None:
                # No levels available, mark as expired
                sig.state = "EXPIRED"
                sig.outcome = "EXPIRED"
                sig.exit_price = None
                sig.exit_timestamp = None
                sig.net_pnl_pct = None
                continue

            # Scan forward from the candle after trigger
            entry_price = sig.entry_trigger

            for i in range(trigger_idx + 1, len(self.candles)):
                c = self.candles[i]
                high = c["high"]
                low = c["low"]

                if direction == "LONG":
                    # Check SL first (price dropped to SL)
                    if low <= sl:
                        sig.state = "CLOSED"
                        sig.outcome = "SL_HIT"
                        sig.exit_price = sl
                        sig.exit_timestamp = c["ts"]
                        sig.net_pnl_pct = (sl - entry_price) / entry_price * 100
                        break
                    # Check TP1 (price reached TP1)
                    if high >= tp1:
                        sig.state = "CLOSED"
                        sig.outcome = "TP1_HIT"
                        sig.exit_price = tp1
                        sig.exit_timestamp = c["ts"]
                        sig.net_pnl_pct = (tp1 - entry_price) / entry_price * 100
                        break
                elif direction == "SHORT":
                    # Check SL first (price rose above SL)
                    if high >= sl:
                        sig.state = "CLOSED"
                        sig.outcome = "SL_HIT"
                        sig.exit_price = sl
                        sig.exit_timestamp = c["ts"]
                        sig.net_pnl_pct = (entry_price - sl) / entry_price * 100
                        break
                    # Check TP1 (price dropped to TP1)
                    if low <= tp1:
                        sig.state = "CLOSED"
                        sig.outcome = "TP1_HIT"
                        sig.exit_price = tp1
                        sig.exit_timestamp = c["ts"]
                        sig.net_pnl_pct = (entry_price - tp1) / entry_price * 100
                        break

            # If no TP/SL hit by end of data, mark as open
            if sig.state == "TRIGGERED":
                sig.state = "OPEN"
                sig.outcome = "OPEN"
                sig.exit_price = None
                sig.exit_timestamp = None
                sig.net_pnl_pct = None

    def _build_result(self, signals: list[SignalEvent]) -> ReplayResult:
        """Build the final result object."""
        triggered = [s for s in signals if s.state == "TRIGGERED"]
        tp1_hits = sum(1 for s in signals if s.outcome == "TP1_HIT")
        sl_hits = sum(1 for s in signals if s.outcome == "SL_HIT")
        time_exits = sum(1 for s in signals if s.outcome == "TIME_EXIT")
        invalidated = sum(1 for s in signals if s.outcome == "INVALIDATED")
        open_count = sum(1 for s in signals if s.outcome == "OPEN")

        return ReplayResult(
            symbol=self.symbol,
            timeframe=self.timeframe,
            total_candles=len(self.candles),
            signals=signals,
            triggered_signals=[s for s in signals if s.state == "TRIGGERED"],
            total_signals=len(signals),
            triggered_count=len(triggered),
            tp1_hits=tp1_hits,
            sl_hits=sl_hits,
            time_exits=time_exits,
            invalidated=invalidated,
            open_signals=open_count,
        )