"""Historical market data collection for AFTERBURNER v0.5 signal performance audit.

Collects historical OHLCV candles from BingX public API for the four watchlist
symbols across multiple timeframes. All data collection is read-only and uses
only public endpoints. No authenticated endpoints or order execution is used.

Data is stored in the existing AFTERBURNER SQLite database for replay analysis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .bingx import BingX
from afterburner.storage import Storage


# BingX API limit per klines request
MAX_KLINES_PER_REQUEST = 500

# Timeframe to millisecond conversion
TIMEFRAME_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}


def collect_candles_range(
    bingx: BingX,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """Collect historical klines from BingX for a given time range.

    Paginates through the API using sequential requests. Only candles fully
    contained within [start_ms, end_ms] are returned. No future data is fetched.

    Args:
        bingx: BingX API client instance
        symbol: BingX perpetual symbol (e.g., "GATO-USDT")
        interval: Candle interval ("5m", "15m", "1h")
        start_ms: Start timestamp in milliseconds (inclusive)
        end_ms: End timestamp in milliseconds (inclusive)

    Returns:
        List of candle dicts with keys: ts, open, high, low, close, volume, end
    """
    all_candles: list[dict] = []
    current_start = start_ms

    interval_ms = TIMEFRAME_MS.get(interval, 300000)

    while current_start < end_ms:
        # BingX returns up to MAX_KLINES_PER_REQUEST candles per request
        raw = bingx.collect_candles(symbol, interval, MAX_KLINES_PER_REQUEST)

        if not raw:
            break

        # Filter candles fully within the requested time range
        filtered = []
        for c in raw:
            candle_ts = c["ts"]
            candle_end = c["end"]
            if candle_ts >= start_ms and candle_end <= end_ms:
                filtered.append(c)

        if not filtered:
            # No more candles in range
            break

        all_candles.extend(filtered)

        # Move start forward past the last candle's end
        last_candle = filtered[-1]
        next_start = last_candle["end"] + interval_ms

        if next_start <= current_start:
            # Prevent infinite loop
            break

        current_start = next_start

        # If we got fewer than MAX_KLINES_PER_REQUEST, we've reached the end
        if len(filtered) < MAX_KLINES_PER_REQUEST:
            break

    return all_candles


def collect_symbol_history(
    bingx: BingX,
    symbol: str,
    start_date: str,
    end_date: str,
    timeframes: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Collect historical candles for a symbol across multiple timeframes.

    Args:
        bingx: BingX API client instance
        symbol: BingX perpetual symbol (e.g., "GATO-USDT")
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)
        timeframes: List of timeframe strings (default: ["5m", "15m", "1h"])

    Returns:
        Dict mapping timeframe string to list of candle dicts
    """
    if timeframes is None:
        timeframes = ["5m", "15m", "1h"]

    start_ms = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000) + 86_399_000

    results: dict[str, list[dict]] = {}

    for tf in timeframes:
        if tf not in TIMEFRAME_MS:
            continue
        candles = collect_candles(bingx, symbol, tf, start_ms, end_ms)
        results[tf] = candles

    return results


def store_candles(
    storage: Storage,
    symbol: str,
    timeframe: str,
    candles: list[dict],
) -> int:
    """Store historical candles in the AFTERBURNER SQLite database.

    Uses the existing Storage.save_candles method which handles deduplication
    via the PRIMARY KEY on (venue, instrument, timeframe, open_ts).

    Args:
        storage: AFTERBURNER Storage instance
        symbol: BingX perpetual symbol
        timeframe: Timeframe string ("5m", "15m", "1h")
        candles: List of candle dicts from collect_candles

    Returns:
        Number of new candles inserted (deduplicated)
    """
    with storage.conn() as db:
        existing = db.execute(
            "SELECT COUNT(*) FROM ohlcv_candles "
            "WHERE venue=? AND instrument=? AND timeframe=?",
            ("bingx", symbol, timeframe),
        ).fetchone()[0]

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    storage.save_candles("bingx", symbol, timeframe, candles, now_ms=now_ms)

    with storage.conn() as db:
        new_count = db.execute(
            "SELECT COUNT(*) FROM ohlcv_candles "
            "WHERE venue=? AND instrument=? AND timeframe=?",
            ("bingx", symbol, timeframe),
        ).fetchone()[0]

    return new_count - existing