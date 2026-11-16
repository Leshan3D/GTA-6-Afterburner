"""BingX PUBLIC perpetual market data. No order endpoints or API credentials."""
from __future__ import annotations
import datetime as dt
from .net import DataError

BASE = "https://open-api.bingx.com"
PATHS = {
    "contracts": "/openApi/swap/v2/quote/contracts",
    "ticker": "/openApi/swap/v2/quote/ticker",
    "book": "/openApi/swap/v2/quote/depth",
    "oi": "/openApi/swap/v2/quote/openInterest",
    "premium": "/openApi/swap/v2/quote/premiumIndex",
    "candles": "/openApi/swap/v3/quote/klines",
}


def finite_number(value):
    try:
        v = float(value)
        if not (-float("inf") < v < float("inf")):
            return None
        return v
    except (TypeError, ValueError, OverflowError):
        return None


def parse_depth_ladders(depth: dict) -> dict:
    """Normalize raw BingX depth response into sorted bid/ask ladders.

    Returns dict with 'bids' and 'asks', each a list of [price, qty] pairs
    sorted appropriately (bids descending, asks ascending). Invalid levels
    are skipped. Never mutates the input.
    """
    bids = []
    asks = []

    raw_bids = depth.get("bids") or []
    raw_asks = depth.get("asks") or []

    for level in raw_bids:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        try:
            px = float(level[0])
            qty = float(level[1])
        except (TypeError, ValueError):
            continue
        if px <= 0 or qty <= 0:
            continue
        bids.append([px, qty])

    for level in raw_asks:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        try:
            px = float(level[0])
            qty = float(level[1])
        except (TypeError, ValueError):
            continue
        if px <= 0 or qty <= 0:
            continue
        asks.append([px, qty])

    # Bids descending, asks ascending
    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    return {"bids": bids, "asks": asks}


def book_metrics(depth):
    try:
        bids = [(float(x[0]), float(x[1])) for x in depth["bids"]]
        asks = [(float(x[0]), float(x[1])) for x in depth["asks"]]
        bids = [(p, q) for p, q in bids if p > 0 and q > 0]
        asks = [(p, q) for p, q in asks if p > 0 and q > 0]
        best_bid = max(p for p, _ in bids)
        best_ask = min(p for p, _ in asks)
        if best_ask < best_bid:
            raise ValueError("crossed order book")
        middle = (best_bid + best_ask) / 2
        return {"bid": best_bid, "ask": best_ask,
                "spread_pct": (best_ask-best_bid)/middle*100,
                "bid_depth_usd": sum(p*q for p, q in bids),
                "ask_depth_usd": sum(p*q for p, q in asks)}
    except (KeyError, ValueError, TypeError) as exc:
        raise DataError(f"Invalid depth response: {exc}") from exc


def candles_normalized(candles):
    """BingX may return newest-first. Normalize ascending; ignore still-open bar."""
    out = []
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp()*1000)
    for x in candles if isinstance(candles, list) else []:
        if isinstance(x, list) and len(x) >= 7:
            vals = [finite_number(z) for z in x[:7]]
            if any(v is None for v in vals) or vals[0] > now_ms:
                continue
            out.append({"ts": int(vals[0]), "open": vals[1], "high": vals[2],
                        "low": vals[3], "close": vals[4], "volume": vals[5],
                        "end": int(vals[6])})
        elif isinstance(x, dict):
            vals = [finite_number(x.get(k)) for k in ("time", "open", "high", "low", "close", "volume")]
            if any(v is None for v in vals):
                continue
            out.append({"ts": int(vals[0]), "open": vals[1], "high": vals[2],
                        "low": vals[3], "close": vals[4], "volume": vals[5],
                        "end": int(vals[0])})
    out = sorted(out, key=lambda row: row["ts"])
    return list({row["ts"]: row for row in out}.values())


class BingX:
    def __init__(self, http):
        self.http = http

    def query(self, endpoint, **params):
        response = self.http.get(BASE + PATHS[endpoint], params=params or None)
        if not isinstance(response, dict) or str(response.get("code")) != "0":
            raise DataError(f"BingX {endpoint}: code={response.get('code') if isinstance(response,dict) else 'malformed'}, msg={response.get('msg') if isinstance(response,dict) else 'invalid'}")
        return response.get("data")

    def collect_candles(self, symbol, interval='5m', limit=500):
        """Return normalized candles for a single timeframe; callers verify close times."""
        if interval not in ('5m','15m','1h'):
            raise ValueError('Unsupported timeframe')
        if limit<1 or limit>500:
            raise ValueError('Candle limit out of bounds')
        return candles_normalized(self.query('candles',symbol=symbol,interval=interval,limit=limit))

    def collect(self, symbol):
        """Errors in one subfeed do not poison others; record each independently."""
        output = {"symbol": symbol, "source_health": {}, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        try:
            contracts = self.query("contracts", symbol=symbol)
            contract = next((c for c in contracts if c.get("symbol") == symbol), None) if isinstance(contracts, list) else contracts
            active = bool(contract and str(contract.get("status", "0")) == "1" and contract.get("symbol") == symbol)
            output["contract_active"] = active
            output["source_health"]["contracts"] = "ok" if active else "inactive_or_absent"
        except (DataError, TypeError) as exc:
            output["contract_active"] = False
            output["source_health"]["contracts"] = str(exc)
        for name, params in [("ticker", {"symbol": symbol}),
                             ("book", {"symbol": symbol, "limit": 20}),
                             ("oi", {"symbol": symbol}),
                             ("premium", {"symbol": symbol}),
                             ("candles", {"symbol": symbol, "interval": "5m", "limit": 100})]:
            try:
                result = self.query(name, **params)
                if isinstance(result, list) and name not in ("candles",):
                    result = next((r for r in result if r.get("symbol") == symbol), None)
                if not result:
                    raise DataError("Empty response")
                output[name] = result
                output["source_health"][name] = "ok"
            except (DataError, TypeError, AttributeError) as exc:
                output["source_health"][name] = str(exc)
        ticker = output.get("ticker") or {}
        premium = output.get("premium") or {}
        oi = output.get("oi") or {}
        output["price"] = finite_number(ticker.get("lastPrice"))
        output["change_24h_pct"] = finite_number(ticker.get("priceChangePercent"))
        output["volume_24h_usd"] = finite_number(ticker.get("quoteVolume"))
        output["open_interest_base"] = finite_number(oi.get("openInterest"))
        output["open_interest_usd"] = ((output["open_interest_base"] or 0)*output["price"]
                                       if output["open_interest_base"] is not None and output["price"] else None)
        output["mark_price"] = finite_number(premium.get("markPrice"))
        output["index_price"] = finite_number(premium.get("indexPrice"))
        output["funding_rate_pct"] = (finite_number(premium.get("lastFundingRate")) or 0)*100 if premium.get("lastFundingRate") is not None else None
        output["candles"] = candles_normalized(output.get("candles"))
        if output.get("book"):
            try:
                raw_book = output["book"]
                output.update(book_metrics(raw_book))
                # Preserve raw depth ladders for execution quality assessment
                parsed = parse_depth_ladders(raw_book)
                output["bids_ladder"] = parsed["bids"]
                output["asks_ladder"] = parsed["asks"]
                # Preserve exchange-provided timestamp for quote freshness checks
                raw_time = raw_book.get("time")
                if raw_time is not None:
                    output["quote_time"] = int(float(raw_time))
            except DataError as exc:
                output["source_health"]["book"] = str(exc)
        output.pop("book", None)  # save only book-derived metrics, not full depth
        return output
