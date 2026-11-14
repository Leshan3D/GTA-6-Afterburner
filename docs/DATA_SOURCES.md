# Data sources and API contracts

- BingX public USDT-M market routes: https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-market/api-reference.md
  - base `https://open-api.bingx.com`
  - `/openApi/swap/v2/quote/contracts?symbol=GATO-USDT`
  - `/openApi/swap/v2/quote/ticker?symbol=GATO-USDT`
  - `/openApi/swap/v2/quote/depth?symbol=GATO-USDT&limit=20`
  - `/openApi/swap/v2/quote/openInterest?symbol=GATO-USDT`
  - `/openApi/swap/v2/quote/premiumIndex?symbol=GATO-USDT`
  - `/openApi/swap/v3/quote/klines?symbol=GATO-USDT&interval=5m&limit=100`
  - Public read-only endpoints; no API key or signature. Some endpoints are throttled to 1 request/s/IP; `HTTP` paces each call conservatively. **On-host smoke test required** due no network access in authoring environment.
  - `openInterest` in base units. Funding rate decimal converted to percent. Other useful fields include quoteVolume, contract status and priceChangePercent. Do not confuse with percent fractions or spot pair liquidity.
- DEX Screener official API: https://docs.dexscreener.com/api/reference
  - `GET https://api.dexscreener.com/token-pairs/v1/solana/{candidate_mint}`
  - Data for token mint is NOT proof that BingX references that token; separately verify. Reject pair if `baseToken.address` differs from requested mint.
- Game narrative discovery feeds: Google News RSS keyword queries, and optional Reddit RSS (may return 403/429). Separate source health indicates failures. These are NOT authoritative confirmation of official game news. Manually verify releases and timing at https://www.rockstargames.com/newswire and https://www.take2games.com/ir.

## Failure behavior

- Individual BingX subfeed failures leave source-health warnings and block required inputs; no fake zero prices or invented funding.
- DEX failures do not substitute similarly named token pools.
- No fetched social activity results in no social score; gaming headlines are attention context only.
- Stale/sparse candles, insufficient book depth, high spreads and contract mismatch produce blocking flags.
- API endpoint changes require changing parser and adding a regression test, never returning hardcoded market prices.
