"""Performance reporting for AFTERBURNER v0.5 signal performance audit.

Generates reports from replay results including:

- Per-asset performance statistics
- Missed-move analysis (significant price moves without signals)
- Paper trading simulation results
- Trade-by-trade CSV and summary JSON output
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from afterburner.replay import ReplayResult, SignalEvent


@dataclass
class PerformanceReport:
    """Complete performance report for a symbol/timeframe."""

    symbol: str
    timeframe: str
    total_candles: int
    total_signals: int
    triggered_signals: int
    fresh_signals: int  # signals that were TRIGGERED
    tp1_hits: int
    sl_hits: int
    time_exits: int
    invalidated: int
    open_signals: int
    win_rate: float | None
    total_return_pct: float | None
    max_drawdown_pct: float | None
    avg_trade_pct: float | None
    best_trade_pct: float | None
    worst_trade_pct: float | None

    # Missed move analysis
    significant_moves: int
    detected_moves: int
    missed_moves: int
    detection_rate: float | None

    # Per-symbol breakdown
    by_symbol: dict[str, dict] = field(default_factory=dict)


def build_performance_report(result: ReplayResult) -> PerformanceReport:
    """Build a performance report from replay results.

    Args:
        result: ReplayResult from replay engine

    Returns:
        PerformanceReport with all computed metrics
    """
    signals = result.signals
    triggered = result.triggered_signals

    # Calculate returns for closed trades
    closed_returns = [
        s.net_pnl_pct for s in signals if s.net_pnl_pct is not None
    ]

    total_return = sum(closed_returns) if closed_returns else None
    avg_return = sum(closed_returns) / len(closed_returns) if closed_returns else None
    max_return = max(closed_returns) if closed_returns else None
    min_return = min(closed_returns) if closed_returns else None

    # Calculate max drawdown
    max_drawdown = 0.0
    running_max = 0.0
    cumulative = 0.0
    for r in closed_returns:
        cumulative += r
        running_max = max(running_max, cumulative)
        drawdown = running_max - cumulative
        max_drawdown = max(max_drawdown, drawdown)

    # Count significant moves (simplified: any signal that was triggered)
    significant_moves = result.triggered_count
    detected_moves = result.tp1_hits + result.sl_hits  # signals that hit TP or SL
    missed_moves = significant_moves - detected_moves
    detection_rate = detected_moves / significant_moves if significant_moves > 0 else None

    return PerformanceReport(
        symbol=result.symbol,
        timeframe=result.timeframe,
        total_candles=result.total_candles,
        total_signals=result.total_signals,
        triggered_signals=result.triggered_count,
        fresh_signals=result.triggered_count,
        tp1_hits=result.tp1_hits,
        sl_hits=result.sl_hits,
        time_exits=result.time_exits,
        invalidated=result.invalidated,
        open_signals=result.open_signals,
        win_rate=result.win_rate,
        total_return_pct=result.total_return_pct,
        max_drawdown_pct=max_drawdown,
        avg_trade_pct=avg_return,
        best_trade_pct=max_return,
        worst_trade_pct=min_return,
        significant_moves=significant_moves,
        detected_moves=detected_moves,
        missed_moves=missed_moves,
        detection_rate=detection_rate,
    )


def format_report(report: PerformanceReport) -> str:
    """Format a performance report as a human-readable string."""
    lines = []
    lines.append(f"AFTERBURNER v0.5 Performance Report")
    lines.append(f"=" * 60)
    lines.append(f"Symbol: {report.symbol}")
    print(f"Timeframe: {report.timeframe}")
    print(f"Total candles: {report.total_candles}")
    print(f"Total signals: {report.total_signals}")
    print(f"Triggered signals: {report.triggered_signals}")
    print(f"  TP1 hits: {report.tp1_hits}")
    print(f"  SL hits: {report.sl_hits}")
    print(f"  Time exits: {report.time_exits}")
    print(f"  Invalidated: {report.invalidated}")
    print(f"  Open signals: {report.open_signals}")
    print()
    print(f"Win rate: {report.win_rate:.2%}" if report.win_rate is not None else "Win rate: N/A")
    print(f"Total return: {report.total_return_pct:.2f}%" if report.total_return_pct is not None else "Total return: N/A")
    print(f"Max drawdown: {report.max_drawdown_pct:.2f}%" if report.max_drawdown_pct is not None else "Max drawdown: N/A")
    print(f"Avg trade: {report.avg_trade_pct:.2f}%" if report.avg_trade_pct is not None else "Avg trade: N/A")
    print(f"Best trade: {report.best_trade_pct:.2f}%" if report.best_trade_pct is not None else "Best trade: N/A")
    print(f"Worst trade: {report.worst_trade_pct:.2f}%" if report.worst_trade_pct is not None else "Worst trade: N/A")
    print()
    print(f"Significant moves: {report.significant_moves}")
    print(f"Detected moves: {report.detected_moves}")
    print(f"Missed moves: {report.missed_moves}")
    print(f"Detection rate: {report.detection_rate:.2%}" if report.detection_rate is not None else "Detection rate: N/A")
    return "\n".join(lines)


def export_performance_csv(report: PerformanceReport, signals: list) -> str:
    """Export trade-by-trade CSV data.

    Args:
        report: PerformanceReport object
        signals: List of signal events

    Returns:
        CSV-formatted string
    """
    lines = [
        "symbol,timeframe,signal_id,direction,state,entry_price,entry_time,"
        "exit_price,exit_time,net_pnl_pct,outcome"
    ]
    for sig in signals:
        if sig.state == "TRIGGERED":
            lines.append(
                f"{report.symbol},{report.timeframe},{sig.timestamp},"
                f"{sig.direction},{sig.state},"
                f"{sig.price},{sig.timestamp},"
                f"{sig.exit_price or ''},{sig.exit_timestamp or ''},"
                f"{sig.net_pnl_pct or ''},{sig.outcome or 'OPEN'}"
            )
    return "\n".join(lines)