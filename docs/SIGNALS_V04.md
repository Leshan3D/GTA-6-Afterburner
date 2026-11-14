# AFTERBURNER v0.4.0 — Signal Engine

A standalone, unauthenticated, research-only futures signal system. It **never places orders**.

## Core behavior

- Collect completed BingX `5m`, `15m`, and `1h` candles; deduplicate into SQLite.
- Use 5m close-confirmed EMA12/26 + 20-bar structural breakout/breakdown + volume confirmation for initial technical triggers. Defaults are research hypotheses, not optimized strategy parameters.
- Require an active contract, usable current quote, spread at most `risk.max_spread_pct`, each side's depth at least `risk.min_book_depth_usd_each_side`, adequate 24h quote volume, >=100 consecutive/available bars, current completed candle, and estimated net R:R >= `signal.min_net_rr`. Missing or failed gates force `FORMING`, never `TRIGGERED`.
- Use BingX futures candles for entries, SL and targets; NEVER use unverified DEX price to derive execution levels.
- SL based on max(1.5 ATR14, distance to structural opposite boundary); TP1 at 2R and TP2 at 3R before costs; report cost-adjusted TP1 R:R.
- A `TRIGGERED` signal **is not a filled trade**. Paper simulation on the next completed candle's open (adverse slippage) may record `PAPER_FILLED`, `UNFILLED`, then `CLOSED`. Historical ambiguity chooses stop before TP. Signal events are saved independently.
- `confidence` is an evidence-index (not win probability). Existing narrative and research scores remain separate.

## Kronos, the non-voting third opinion

Kronos defaults `enabled: false` until Omarchy verifies an independent model environment. `factor_weight: 0.0` is hard-enforced in the adapter. When enabled, an isolated subprocess reads up to 400 completed OHLCV candles and predicts 6x5m, 8x15m, 6x1h bars; predictions are cached by cutoff and saved in SQLite. The runner uses CPU, `NeoQuasar/Kronos-small` and `NeoQuasar/Kronos-Tokenizer-base`. A model failure produces an unavailable status and never halts collection.

Create a **separate** Python virtual environment and make upstream `model` importable within that environment. Example reference sources:

- https://github.com/shiyu-coder/Kronos
- https://huggingface.co/NeoQuasar/Kronos-small
- https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base

Set `kronos.python` in `config/settings.yaml` to that venv's Python path only after checking its installed dependencies and CPU inference. The application's default environment remains unchanged. There is no dependency on JAFAR files or processes. The runner only produces an independent forecast; it does not vote, size positions, or change triggered signals.

## CLI and Telegram

- `python -m afterburner signals` — triggered, active analytical signals
- `python -m afterburner setups` — last observed forming and armed setups
- `python -m afterburner performance` — paper-fill statistics, not real P&L
- `python -m afterburner backtest` — historical candle simulation. On insufficient history, reports that instead of fabricating results.
- `python -m afterburner kronos` — latest stored model opinions

Telegram: `/signals`, `/signal GATO`, `/setups`, `/performance`, `/kronos`. Existing commands remain available.

## Data migration, backup, rollbacks

Migration adds tables using `CREATE TABLE IF NOT EXISTS`; it does not delete or alter existing tables. **Back up `~/.local/state/afterburner/afterburner.db` using SQLite's online backup API before the first v0.4 run.** For rollback, stop AFTERBURNER and restore the backup and prior source deployment; do not simply drop new tables.

## Notes and limitations

This first strategy version is `afterburner-0.4.0-breakout-v1`. It has not demonstrated profitability. Its confidence is not calibrated; 100-bar warm-up is a minimum for technical signals, while 400 bars is the model's preferred context. Paper results are based on OHLCV and estimated costs, not actual depth and execution. Historical backtests cannot reconstruct real spreads or order-book depth and expressly disclose this assumption. No slippage model can eliminate gap, liquidation or market-manipulation risk.

BingX's 5m/15m/1h interval formats and 500-candle limit must be verified live on Omarchy before enabling the upgraded service. If unsupported, report a provider compatibility issue rather than inventing candles. Public payloads remain explicit allowlists and never include tokens, account balances, API keys or private local paths.
