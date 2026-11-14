"""DEX Screener public Solana pool monitor: strict mint matching."""
from __future__ import annotations
from .bingx import finite_number
from .net import DataError

BASE = "https://api.dexscreener.com/token-pairs/v1"


def parse_pools(raw, expected_mint: str):
    if not isinstance(raw, list):
        raise DataError("DEX response is not a pool array")
    pools = []
    for pool in raw:
        if not isinstance(pool, dict) or pool.get("chainId") != "solana":
            continue
        base = pool.get("baseToken") or {}
        if base.get("address") != expected_mint:
            continue  # Never score a similarly named or quote-side token.
        liquidity = finite_number((pool.get("liquidity") or {}).get("usd"))
        volume = finite_number((pool.get("volume") or {}).get("h24"))
        txs = (pool.get("txns") or {}).get("h24") or {}
        pools.append({"dex": pool.get("dexId"), "pair_address": pool.get("pairAddress"),
                      "url": pool.get("url"), "quote_symbol": (pool.get("quoteToken") or {}).get("symbol"),
                      "price_usd": finite_number(pool.get("priceUsd")),
                      "liquidity_usd": liquidity, "volume_24h_usd": volume,
                      "buys_24h": txs.get("buys"), "sells_24h": txs.get("sells"),
                      "change_24h_pct": finite_number((pool.get("priceChange") or {}).get("h24")),
                      "boosts_active": (pool.get("boosts") or {}).get("active")})
    pools.sort(key=lambda x: x["liquidity_usd"] if x["liquidity_usd"] is not None else -1, reverse=True)
    primary = pools[0] if pools else None
    return {"matched_pools": len(pools), "primary": primary,
            "other_pools": pools[1:5],
            # Pool liquidity is not summed; an LP pair may be double-counted elsewhere.
            "pool_selection": "largest_verified_base_mint_pool"}


class DexScreener:
    def __init__(self, http):
        self.http = http

    def collect(self, asset):
        if not asset.candidate_mint:
            return {"status": "missing_mint", "mint_verified": False}
        try:
            raw = self.http.get(f"{BASE}/{asset.dex_chain}/{asset.candidate_mint}")
            pools = parse_pools(raw, asset.candidate_mint)
            pools.update({"status": "ok" if pools["matched_pools"] else "no_matching_pools",
                          "mint_verified": asset.mint_verified,
                          "candidate_mint": asset.candidate_mint,
                          "identity_evidence": asset.verification_evidence or None})
            return pools
        except DataError as exc:
            return {"status": "error", "error": str(exc), "mint_verified": asset.mint_verified}
