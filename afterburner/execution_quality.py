"""Execution-quality assessment from BingX public order-book data.

Uses actual best bid and best ask from the same order-book snapshot.
Estimates round-trip costs for hypothetical notionals without assuming
limit-order fills or accessing authenticated APIs.
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


def assess_execution(market: dict, dex: dict | None = None,
                     notionals: list[float] | None = None,
                     config: dict | None = None) -> dict:
    """Assess execution quality from public BingX depth data.

    Returns a dict with per-notional cost estimates and a quality grade:
    ACCEPTABLE / CAUTION / POOR_LIQUIDITY / DATA_UNAVAILABLE.

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

    best_bid = _finite(market.get("bid"))
    best_ask = _finite(market.get("ask"))
    bid_depth_usd = _finite(market.get("bid_depth_usd"))
    ask_depth_usd = _finite(market.get("ask_depth_usd"))
    spread_pct = _finite(market.get("spread_pct"))

    # Validate spread from same bid/ask snapshot.
    spread_valid = False
    spread_source = "market.spread_pct"
    if best_bid is not None and best_ask is not None and best_ask > best_bid > 0:
        mid = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid * 100
        spread_source = "best_bid/best_ask (same snapshot)"
        spread_valid = True
    elif spread_pct is not None and spread_pct > 0:
        spread_valid = True

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    observations = []

    for notional in notionals:
        est = {
            "notional_usd": notional,
            "spread_cost_pct": round(spread_pct, 4) if spread_pct is not None else None,
            "buy_impact_pct": None,
            "sell_impact_pct": None,
            "fees_pct": round(2 * fee_bps / 100, 4),
            "total_round_trip_buy_pct": None,
            "total_round_trip_sell_pct": None,
        }

        if not spread_valid:
            observations.append(est)
            continue

        # Depth-based slippage: impact scales with fraction of depth consumed.
        # Buying consumes asks; selling consumes bids.
        if ask_depth_usd and ask_depth_usd > 0:
            buy_fill_frac = min(notional / ask_depth_usd, 1.0)
            est["buy_impact_pct"] = round(buy_fill_frac * spread_pct, 4)
        if bid_depth_usd and bid_depth_usd > 0:
            sell_fill_frac = min(notional / bid_depth_usd, 1.0)
            est["sell_impact_pct"] = round(sell_fill_frac * spread_pct, 4)

        if est["buy_impact_pct"] is not None:
            est["total_round_trip_buy_pct"] = round(
                spread_pct + est["buy_impact_pct"] + 2 * fee_bps / 100, 4
            )
        if est["sell_impact_pct"] is not None:
            est["total_round_trip_sell_pct"] = round(
                spread_pct + est["sell_impact_pct"] + 2 * fee_bps / 100, 4
            )

        observations.append(est)

    # Quality determination based on worst-case (largest notional) total cost.
    worst_cost = None
    for est in observations:
        for key in ("total_round_trip_buy_pct", "total_round_trip_sell_pct"):
            v = est[key]
            if v is not None:
                worst_cost = v if worst_cost is None else max(worst_cost, v)

    if not spread_valid:
        quality = "DATA_UNAVAILABLE"
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
    if bid_depth_usd is None or ask_depth_usd is None:
        warnings.append("order-book depth unavailable")
    elif (bid_depth_usd or 0) < 500 or (ask_depth_usd or 0) < 500:
        warnings.append("thin order-book depth")
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
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }
