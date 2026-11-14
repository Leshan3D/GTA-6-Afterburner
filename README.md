# AFTERBURNER v0.3.0 — GTA narrative intelligence, Telegram status, public preview

**Independent of other local engines. Read-only market research; never an auto-trader.** AFTERBURNER monitors four speculative GTA-themed BingX USDT perpetual markets, associated *candidate* Solana DEX pools and narrative coverage. It makes no investment recommendations, cannot send exchange orders and does not require exchange credentials.

This release adds: **private allowlisted Telegram status bot**, `/progress` and diagnostics, a **sanitized public-only dashboard**, a **GitHub Pages snapshot exporter**, and a static-site deploy workflow.

## Quick links

- **Set up the Telegram bot:** [`docs/TELEGRAM.md`](docs/TELEGRAM.md).
- **Show a public URL:** [`docs/PUBLIC_SHARING.md`](docs/PUBLIC_SHARING.md) (Tailscale Funnel, GitHub Pages, or Cloudflare demo tunnel).
- **New local install:** [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
- **Market feed details:** [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

## Features

- Public BingX contract active check, price/24h volume, mark/index/funding, OI, 5m candles and top-20 order-book metrics.
- DEX Screener pool lookup by candidate Solana mint, strict `baseToken.address` match, pool liquidity/volume, quote-asset context. **All four mint-to-perp mappings remain unverified by default.**
- GTA/Grand Theft Auto/Rockstar Google News RSS and optional Reddit RSS, headline deduplication, source-health tracking and separation of game news from token-specific news.
- Separate long/short exploratory scores plus data-quality blockers for contract, liquidity, identity, spread and freshness. All research states remain **non-executable**.
- SQLite WAL store for market history, headline discovery, source health, Telegram cooldown and polling offset.
- Private local dashboard on `127.0.0.1:5056`, plus distinct public-only read-only preview on `127.0.0.1:5057` serving **only** `/`, `/index.html`, and `/data.json`. Private API and health details are never copied into the public payload.
- Telegram: `/progress`, `/status`, `/meme`, `/meme GATO`, `/health`, `/news`, `/alerts`, `/link`, `/help`. Explicit allowlist, no anonymous access, push alerts only to the operator. Bot polling runs in a thread independent of market collection.
- Static `site/` directory and `python -m afterburner export-public` to publish a manually refreshed snapshot via the included GitHub Actions Pages workflow.

## Installation

Extract the release archive into your chosen project directory, then:

```bash
bash ops/install_omarchy.sh
python -m afterburner doctor   # static checks; no network
systemctl --user enable --now afterburner.service
```

The installer creates an isolated Python venv, a SQLite database, and a user-level systemd service unit — all outside the project code directory. Credentials are configured in `~/.config/afterburner/.env` (mode `600`) and never committed. The service starts neither the Telegram bot nor alerts until a valid bot token and allowed chat ID are configured.

For upgrades, **do not blindly overwrite the existing `config/*.yaml`** or the current SQLite database. Use `OPENCODE_UPGRADE.md`.

## CLI

```bash
python -m afterburner doctor        # static checks; no network
python -m afterburner progress      # collector and freshness summary
python -m afterburner status        # service heartbeat
python -m afterburner once          # one live market/news pass; service MUST be stopped
python -m afterburner bot-chats     # discover allowed chat IDs; service MUST be stopped
python -m afterburner export-public # write site/data.json from SQLite; no network
```

To publish on GitHub Pages, commit and push the sanitized `site/data.json` to your own public repository, then select **Settings → Pages → Source: GitHub Actions**. Pages does **not** run Python and cannot supply live data unless an external publisher updates the repository. For a live shareable URL while your host is online, use Funnel on **public preview port 5057 only**.

## Testing

```bash
python -m unittest discover -s tests -v
python -m compileall -q afterburner
```

Tests are deterministic and offline: they check market parsing, scoring gates, Telegram command access control and public-site data isolation. Live BingX/DEX APIs, Telegram delivery, systemd and public tunnel behavior **must be smoke-tested on your host**.

## Security and research constraints

1. **Never tunnel `:5056`.** It exposes private source diagnostics; share only the sanitized public view on `:5057`.
2. **Never commit secrets** — `.env`, private home configuration, SQLite, logs, API tokens or chat IDs. The public static exporter uses an explicit allowlist of fields.
3. **Unknown Telegram chats are ignored.** Friends can only query the status bot if their chat IDs are explicitly added; they do not automatically receive push alerts.
4. **Coin tickers are not proof of mint identity.** The default `mint_verified: false` must not be changed without credible, per-market evidence. A token-matched DEX pool is not automatically the underlying asset of a futures product.
5. **News isn't price direction.** Gaming headlines are discovery evidence, not automatically positive signals. Social-platform metrics are not fabricated; X/Telegram social listening isn't included.
6. **No trading capability.** AFTERBURNER has no Binance/BingX private API calls, trade buttons, leverage actions, order approvals or execution module.
7. **Sunset is simple:** `systemctl --user disable --now afterburner.service`; shut down Funnel separately; archive the independent SQLite database if desired.

## v0.3.0 — Automatic GitHub Pages snapshots (optional)

The public dashboard now shows collection health, the most recent market observation,
and when the public snapshot was prepared. It computes staleness from market observation
timestamps; exporting or publishing cannot make old prices appear fresh.

To install the **optional** ten-minute public publisher, read
[`docs/AUTO_PUBLISHING.md`](docs/AUTO_PUBLISHING.md). It uses a dedicated Git
checkout and commits only `site/data.json`. It is **not enabled by default**.
The private Telegram bot adds `/publish` for the last local push status. A successful
git push is not proof GitHub Pages finished deploying; verify the Pages workflow.

The live market collector and JAFAR are not modified by this publishing component.

## v0.4.0 — Candle-close signal intelligence (operator review required)

New: stored multi-timeframe completed BingX candles, confirmed technical LONG/SHORT triggers, entry/SL/TP/net-R:R levels, separable hypothetical next-bar paper fills, backtest research, Telegram `/signals`/`/setups`/`/performance`/`/kronos`, and a third-opinion Kronos subprocess (disabled until locally installed; zero voting weight). **No exchange order execution.**

See [v0.4 signal design and limitations](docs/SIGNALS_V04.md) and [OpenCode deployment guide](OPENCODE_V0.4_UPGRADE.md). Existing data store and public publisher are retained.
