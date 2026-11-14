"""Transparent, exploratory long/short screen. Never an order recommendation."""
from __future__ import annotations
from datetime import datetime, timezone
from .bingx import finite_number


def clamp(value, lo=0, hi=100):
    return min(hi, max(lo, value))


def pct(new, old):
    if new is None or old is None or old <= 0:
        return None
    return (new / old - 1) * 100


def momentum_features(market):
    candles = market.get("candles") or []
    # Ignore current incomplete 5m candle; malformed/missing bar blocks signal.
    now_ms = datetime.now(timezone.utc).timestamp()*1000
    closed = [c for c in candles if c.get("end", 0) <= now_ms and c.get("close", 0) > 0]
    if len(closed) < 30:
        return {"ready": False, "closed_candles": len(closed)}
    c = closed[-1]["close"]
    ret15 = pct(c, closed[-4]["close"])
    ret60 = pct(c, closed[-13]["close"])
    recent = [x["volume"] for x in closed[-3:]]
    baseline = [x["volume"] for x in closed[-27:-3]]
    baseavg = sum(baseline)/len(baseline) if baseline else 0
    volratio = sum(recent)/3/baseavg if baseavg > 0 else None
    return {"ready": True, "ret_15m_pct": ret15, "ret_60m_pct": ret60,
            "volume_ratio_15m": volratio, "closed_candles": len(closed),
            "last_closed_at": closed[-1].get("end")}


def evaluate(asset, market, dex, previous, config, recent_news):
    """Independent directional scores; missing evidence is never silently invented."""
    risk = config["risk"]
    features = momentum_features(market)
    problems = []
    notes = []
    score_long, score_short = 0, 0
    verified = dex.get("mint_verified") is True and asset.mint_verified

    if not market.get("contract_active"):
        problems.append("bingx_contract_unverified_or_inactive")
    if not market.get("price"):
        problems.append("missing_futures_price")
    if not features.get("ready"):
        problems.append("insufficient_closed_5m_candles")
    if features.get("last_closed_at"):
        age = (datetime.now(timezone.utc).timestamp()*1000 - features["last_closed_at"])/1000
        if age > config["schedule"]["max_market_age_seconds"] or age < -30:
            problems.append("stale_or_future_ohlcv_data")
    if not verified:
        problems.append("dex_to_futures_identity_unverified")
    if dex.get("status") != "ok":
        notes.append("dex_data_missing_or_invalid")

    ret15 = features.get("ret_15m_pct")
    ret60 = features.get("ret_60m_pct")
    vr = features.get("volume_ratio_15m")
    if ret15 is not None and ret60 is not None:
        for direction in (1, -1):
            p15, p60 = direction*ret15, direction*ret60
            score = (15 if p15 >= 1 else 8 if p15 >= 0.4 else 0)
            score += (15 if p60 >= 3 else 8 if p60 >= 1 else 0)
            score += (15 if vr is not None and vr >= 1.8 and p15 >= 0.4 else
                      8 if vr is not None and vr >= 1.25 and p15 >= 0.4 else 0)
            if direction == 1:
                score_long += score
            else:
                score_short += score

    prev_market = previous.get("market") if previous else {}
    old_oi = prev_market.get("open_interest_base") if prev_market else None
    new_oi = market.get("open_interest_base")
    oi_change = pct(new_oi, old_oi)
    # Only compare consecutive observations within a reasonable elapsed window.
    if previous:
        try:
            old_ts = datetime.fromisoformat(previous["collected_at"])
            if not 0 < (datetime.now(timezone.utc)-old_ts).total_seconds() < 1800:
                oi_change = None
        except (KeyError, ValueError, TypeError):
            oi_change = None
    if oi_change is not None and oi_change >= 1.0:
        if (ret15 or 0) > 0:
            score_long += 15
        elif (ret15 or 0) < 0:
            score_short += 15
    if oi_change is None:
        notes.append("oi_delta_unavailable_or_not_yet_baselined")
    funding = market.get("funding_rate_pct")
    if funding is not None:
        if funding <= -0.05:
            score_long += 8
        elif funding >= 0.05:
            score_short += 8

    spread = market.get("spread_pct")
    bid_depth = market.get("bid_depth_usd")
    ask_depth = market.get("ask_depth_usd")
    if spread is None or spread > risk["max_spread_pct"]:
        problems.append("missing_or_excessive_perp_spread")
    if bid_depth is None or ask_depth is None or min(bid_depth, ask_depth) < risk["min_book_depth_usd_each_side"]:
        problems.append("inadequate_perp_book_depth")
    if market.get("volume_24h_usd") is None or market["volume_24h_usd"] < risk["min_quote_volume_24h_usd"]:
        problems.append("inadequate_futures_24h_volume")
    if not any(x.startswith("missing_or_excessive_perp_spread") for x in problems) and \
       not any(x.startswith("inadequate_perp_book_depth") for x in problems):
        score_long += 15
        score_short += 15

    primary = dex.get("primary") or {}
    liq = finite_number(primary.get("liquidity_usd"))
    dex_price = finite_number(primary.get("price_usd"))
    divergence = abs(pct(market.get("price"), dex_price)) if pct(market.get("price"), dex_price) is not None else None
    if verified:
        if liq is None or liq < risk["min_dex_liquidity_usd"]:
            problems.append("insufficient_dex_liquidity")
        else:
            score_long += 5
            score_short += 5
        if divergence is None or divergence > risk["max_dex_perp_divergence_pct"]:
            problems.append("dex_perp_price_divergence_or_missing")
        if primary.get("quote_symbol", "").upper() not in ("SOL", "USDC", "USDT"):
            notes.append("dex_quote_asset_risk_" + str(primary.get("quote_symbol")))
    else:
        notes.append("candidate_mint_display_only_not_verified_against_bingx_underlying")

    # Generic game news increases ATTENTION, not bullishness. No unvalidated
    # sentiment model and no positive directional credits for game coverage.
    matching = [x for x in recent_news if asset.symbol in x.get("asset_symbols", [])]
    narrative = [x for x in recent_news if "gta6_narrative" in x.get("asset_symbols", [])]
    narrative_context = {"game_articles_36h": len(narrative),
                         "token_specific_articles_36h": len(matching),
                         "direction": "unverified_attention_only"}
    if matching:
        notes.append("token_specific_news_mentioned_direction_not_inferred")
    legs = int(bool(features.get("ready"))) + int(new_oi is not None) + int(verified and liq is not None)
    if legs < risk.get("min_required_data_legs", 2):
        problems.append("insufficient_independent_evidence_legs")
    score_long = int(clamp(score_long))
    score_short = int(clamp(score_short))
    orientation = "LONG_WATCH" if score_long > score_short else "SHORT_WATCH" if score_short > score_long else "NEUTRAL"
    top_score = max(score_long, score_short)
    if top_score < risk["alert_score_threshold"]:
        state = "MONITOR"
    elif problems:
        state = "RESEARCH_ONLY"
    else:
        state = "REVIEW_CANDIDATE"
    # The project is alert-only: even REVIEW_CANDIDATE is NOT order authorization.
    return {"symbol": asset.symbol, "state": state,
            "bias": orientation, "long_score": score_long, "short_score": score_short,
            "features": {**features, "oi_change_pct": oi_change,
                         "funding_rate_pct": funding, "dex_perp_divergence_pct": divergence},
            "narrative": narrative_context, "blocking_flags": sorted(set(problems)),
            "advisories": sorted(set(notes)), "tradable": False,
            "explanation": "Research signal only. No API execution or order capability."}
