# AFTERBURNER v0.4.0 — OpenCode deployment handoff

**Operator's deployment target:** `/mnt/jormungandr-rw/AFTERBURNER`
**Upstream:** `https://github.com/Leshan3D/GTA-6-Afterburner`
**Patch baseline:** packaged AFTERBURNER v0.3.0. The host's actual `main` may have minor manual-reconciliation differences; do not overwrite them blindly.

## Objective

Deploy ChatGPT's newly written v0.4.0 source patch to the existing working AFTERBURNER v0.3.0 installation. Adds completed multi-timeframe futures candles, signal formation/triggering, SL/TP/R:R, paper outcome tracking, historical backtests, an optional non-voting Kronos subprocess, Telegram commands and public/private signal displays. **No order execution.**

## Procedure

1. Verify current `git status --short`, commit and remote; preserve uncommitted work. Create `feat/signal-intelligence-v040` from the current baseline. Stop only AFTERBURNER before applying or migrating; never touch JAFAR.
2. Download `AFTERBURNER-v0.4.0-PATCH.zip` and unzip its `.patch`. Run `git apply --check` in the working repo. If it fails because the live v0.3 source differs slightly from the archived v0.3 package, inspect and reconcile **individual hunks**, add tests and report the differences. Never force-overwrite existing source or runtime configuration. The full source ZIP is reference only.
3. Run the existing virtual environment's `python -m pytest -q`; **35 tests passed offline in the supplied build**. Run `python -m compileall -q afterburner`, then `python -m afterburner doctor`.
4. Make a consistent SQLite backup before first v0.4 service launch. Keep `~/.config/afterburner/.env`, `~/.local/state/afterburner/afterburner.db`, and the existing Python venv out of Git.
5. Start a controlled **one-off** market collection while the regular service is stopped. Validate 5m, 15m and 1h BingX response shape for each token; check `ohlcv_candles` records, completed-bar timestamps, deduplication and provider rate limiting. If a venue rejects `interval=1h`/`limit=500`, fix only that verified adapter issue and add a test.
6. Run `python -m afterburner signals`, `setups`, `performance`, `backtest`; no synthetic market-history results may appear as performance. Data-quality gates may prevent all signals: this is correct and must not be overridden merely to make the dashboard look active.
7. Check Telegram `/signals`, `/signal GATO`, `/setups`, `/performance`, `/kronos`, plus all existing commands. `/signals` may initially report none.
8. Verify private `http://127.0.0.1:5056/`, sanitized preview `http://127.0.0.1:5057/`, the `export-public` command, public schema validation, and compatibility with the independent GitHub Pages publisher. No public private diagnostics or secret paths.
9. **Optional Kronos validation:** install a completely separate CPU venv under `~/.local/share/afterburner-kronos/venv`, with official model/tokenizer and compatible dependencies. The upstream Kronos Python module must be importable as `model` (or `kronos_src.model`) in the runner process. Validate real inference on 400 contiguous candle bars in a one-shot execution; only then set `kronos.enabled: true` and configure `kronos.python` to the isolated interpreter. Never import anything from JAFAR. Keep `factor_weight: 0.0`.
10. Restart AFTERBURNER, verify service, publisher timer and Pages. Push the code update after passing tests and obtaining operator authorization. The existing public-data publisher should keep working without any new credentials.

## Operational checks

```bash
systemctl --user status afterburner.service --no-pager
systemctl --user status afterburner-publisher.timer --no-pager
python -m afterburner doctor
python -m afterburner signals
python -m afterburner setups
python -m afterburner performance
python -m afterburner kronos
python -m afterburner backtest
python -m pytest -q
```

## Report back

- Git branch, applied files, conflicting patch hunks and modifications made.
- Passed/failed unit tests, BingX OHLCV live fetch and actual stored bar counts by timeframe/token.
- A real computed signal if available, otherwise exact blocking reasons. **Never manufacture a trade.**
- Paper outcome tracking correctness and backtest historical sample length.
- Kronos environment readiness, sample forecast or exact failure (disabled is acceptable until installed).
- Telegram bot, private/public dashboards, public publisher and Pages deployment checks.
- Any gaps requiring ChatGPT's next code revision.

**Do not introduce trading API keys, broker execution, external server exposure, JAFAR imports, live position sizing, or autonomous trading.**
