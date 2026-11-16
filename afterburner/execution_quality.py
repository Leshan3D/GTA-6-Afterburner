"""Execution-quality assessment from BingX public order-book data.

Uses actual best bid and best ask from the same order-book snapshot, with a
VWAP simulation over the real bid/ask depth ladders for hypothetical
notionals of $50, $100 and $250.

Never accesses authenticated endpoints or assumes limit-order fills.
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import isfinite


def _finite(value):
    try:
        v = float(value)
        return v if isfinite(v) and v > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ladder_impact(levels: list[list], side: str, notional_usd: float) -> dict | None:
    """Walk the depth ladder to estimate VWAP execution cost for one side.

    levels: list of [price, quantity] from the same snapshot.
    side: 'buy' consumes asks, 'sell' consumes bids.
    Returns dict with avg_fill_price, impact_pct, filled_qty, filled_cost_usd
    or None if insufficient depth.
    """
    if not levels:
        return None
    remaining = notional_usd
    filled_qty = 0.0
    filled_cost_usd = 0.0
    first_fill_price = None
    last_fill_price = None

    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        try:
            px = float(level[0])
            qty = float(level[1])
        except (TypeError, ValueError):
            continue
        if px <= 0 or qty <= 0:
            continue
        level_cost_usd = px * qty
        if level_cost_usd <= 0:
            continue
        take = min(remaining, level_cost_usd)
        if take <= 0:
            break
        take_qty = take / px
        if first_fill_price is None:
            first_fill_price = px
        last_fill_price = px
        filled_qty += take_qty
        filled_cost_usd += take
        remaining -= take
        if remaining <= 1e-9:
            break

    if remaining > 1e-9 or filled_qty <= 0:
        return None  # insufficient depth

    vwap_fill_price = filled_cost_usd / filled_qty
    if side == 'buy':
        # Buying: we pay more than best ask (impact is adverse)
        best_px = float(levels[0][0]) if levels and levels[0][0] is not None else None
        if best_px is not None and best_px > 0:
            impact_pct = (vwap_fill_price - best_px) / best_px * 100
        else:
            impact_pct = None
    else:
        # Selling: we receive less than best bid (impact is adverse)
        best_px = float(levels[0][0]) if levels and levels[0][0] is not None else None
        if best_px is not None and best_px > 0:
            impact_pct = (best_px - vwap_fill_price) / best_px * 100
        else:
            impact_pct = None

    return {
        'filled_qty': round(filled_qty, 8),
        'filled_cost_usd': round(filled_cost_usd, 4),
        'vwap_fill_price': round(vwap_fill_price, 8),
        'impact_pct': round(impact_pct, 6) if impact_pct is not None else None,
        'sufficient_depth': True,
    }


def assess_execution(market: dict, dex: dict | None = None,
                     notionals: list[float] | None = None,
                     config: dict | None = None) -> dict:
    """Assess execution quality from public BingX depth data.

    Uses actual best bid and best ask from the same order-book snapshot, with
    a VWAP simulation over the real bid/ask depth ladders for hypothetical
    notionals of $50, $100 and $250.

    Returns a dict with per-notional cost estimates and a quality grade:
    ACCEPTABLE / CAUTION / POOR_LIQUIDITY / INSUFFICIENT_DEPTH /
    DATA_UNAVAILABLE.

    Never accesses authenticated endpoints or assumes order fills.
    """
    config = config or {}
    notionals = notionals or [50.0, 100.0, 250.0]
    signal_cfg = config.get("signal", {})
    fee_bps = float(signal_cfg.get("fee_bps", 10))
    slippage_bps = float(signal_cfg.get("slippage_bps", 10))
    acceptable_max = float(signal_cfg.get("acceptable_max_cost_pct", 1.0))
    caution_max = float(signal_cfg.get("caution_max_cost_pct", 2.5))
    poor_max = float(signal_cfg.get("poor_liquidity_max_cost_pct", 5.0))

    # Validate quote freshness using actual timestamp when available.
    quote_ts = _finite(market.get("quote_time") or market.get("updateTime")
                       or market.get("time") or market.get("timestamp"))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    quote_stale = False
    if quote_ts is not None:
        quote_age_ms = now_ms - int(quote_ts)
        if quote_age_ms > 60000:  # quote older than 60 seconds
            quote_stale = True
    # If no timestamp at all, flag the limitation as a warning but don't block
    quote_timestamp_missing = quote_ts is None

    # Parse depth ladders from the same snapshot.
    # bingx.collect() produces bids_ladder/asks_ladder via parse_depth_ladders.
    raw_bids = market.get("bids_ladder") or market.get("bids") or []
    raw_asks = market.get("asks_ladder") or market.get("asks") or []

    # Also accept flat depth metrics as fallback (but flag as less reliable).
    bid_depth_usd = _finite(market.get("bid_depth_usd"))
    ask_depth_usd = _finite(market.get("ask_depth_usd"))

    # Validate spread from best bid/best ask of the same snapshot.
    best_bid = _finite(raw_bids[0][0]) if raw_bids and isinstance(raw_bids[0], (list, tuple)) else _finite(market.get("bid"))
    best_ask = _finite(raw_asks[0][0]) if raw_asks and isinstance(raw_asks[0], (list, tuple)) else _finite(market.get("ask"))

    spread_valid = False
    spread_pct = None
    spread_source = "none"
    if best_bid is not None and best_ask is not None and best_ask > best_bid > 0:
        mid = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid * 100
        spread_valid = True
        spread_source = "best_bid/best_ask (same snapshot)"

    observations = []
    for notional in notionals:
        est = {
            "notional_usd": notional,
            "spread_cost_pct": round(spread_pct, 4) if spread_pct is not None else None,
            "buy_impact_pct": None,
            "sell_impact_pct": None,
            "fees_pct": round(2 * fee_bps / 100, 4),
            "slippage_bps": slippage_bps,
            "total_round_trip_buy_pct": None,
            "total_round_trip_sell_pct": None,
            "sufficient_depth": False,
        }

        if not spread_valid:
            observations.append(est)
            continue

        # Simulate buy side: consume asks ladder.
        buy_ladder = _ladder_impact(raw_asks, 'buy', notional)
        if buy_ladder is not None:
            est["buy_impact_pct"] = buy_ladder["impact_pct"]
            est["buy_sufficient"] = True
        else:
            est["buy_sufficient"] = False

        # Simulate sell side: consume bids ladder.
        sell_ladder = _ladder_impact(raw_bids, 'sell', notional)
        if sell_ladder is not None:
            est["sell_impact_pct"] = sell_ladder["impact_pct"]
            est["sell_sufficient"] = True
        else:
            est["sell_sufficient"] = False

        # Total round-trip cost: spread + one-way impact + fees + slippage (no double-count).
        if est["buy_impact_pct"] is not None:
            est["total_round_trip_buy_pct"] = round(
                spread_pct + est["buy_impact_pct"] + 2 * fee_bps / 100 + 2 * slippage_bps / 100, 4
            )
        if est["sell_impact_pct"] is not None:
            est["total_round_trip_sell_pct"] = round(
                spread_pct + est["sell_impact_pct"] + 2 * fee_bps / 100 + 2 * slippage_bps / 100, 4
            )

        # Both sides must have sufficient depth for this notional.
        est["sufficient_depth"] = bool(est["buy_sufficient"] and est["sell_sufficient"])
        observations.append(est)

    # Quality determination based on worst-case (largest notional) total cost.
    # If either side cannot be priced for any notional, never ACCEPTABLE.
    worst_cost = None
    any_side_insufficient = False
    for est in observations:
        if not est["sufficient_depth"]:
            any_side_insufficient = True
        for key in ("total_round_trip_buy_pct", "total_round_trip_sell_pct"):
            v = est[key]
            if v is not None:
                worst_cost = v if worst_cost is None else max(worst_cost, v)

    if not spread_valid:
        quality = "DATA_UNAVAILABLE"
    elif quote_stale:
        quality = "DATA_UNAVAILABLE"
    elif any_side_insufficient:
        quality = "INSUFFICIENT_DEPTH"
    elif worst_cost is None:
        quality = "DATA_UNAVAILABLE"
    elif worst_cost <= acceptable_max:
        quality = "ACCEPTABLE"
    elif worst_cost <= caution_max:
        quality = "CAUTION"
    elif worst_cost <= poor_max:
        quality = "POOR_LIQUIDITY"
    else:
        quality = "POOR_LIQUIDITY"

    warnings = []
    if not spread_valid:
        warnings.append("bid/ask spread unavailable or crossed")
    if quote_stale:
        warnings.append("quote timestamp stale")
    if quote_timestamp_missing:
        warnings.append("quote timestamp unavailable")
    if any_side_insufficient:
        warnings.append("insufficient depth for requested notionals")
    if worst_cost is not None and worst_cost > caution_max:
        warnings.append("high estimated round-trip cost")

    return {
        "quality": quality,
        "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
        "spread_source": spread_source if spread_valid else "invalid",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_depth_usd": bid_depth_usd,
        "ask_depth_usd": ask_depth_usd,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
        "notionals": observations,
        "worst_case_cost_pct": round(worst_cost, 4) if worst_cost is not None else None,
        "warnings": warnings,
        "quote_stale": quote_stale,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }