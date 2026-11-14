# AFTERBURNER v0.2.0 — GTA narrative intelligence, Telegram status, public preview

**Independent of JAFAR. Read-only market research; never an auto-trader.** AFTERBURNER monitors four speculative GTA-themed BingX USDT perpetual markets, associated *candidate* Solana DEX pools and narrative coverage. It makes no investment recommendations, cannot send exchange orders and does not require exchange credentials.

This release adds: **private allowlisted Telegram status bot**, `/progress` and diagnostics, a **sanitized public-only dashboard at `127.0.0.1:5057`**, a **GitHub Pages snapshot exporter**, and a static-site deploy workflow. Existing local dashboard stays at **`127.0.0.1:5056`**.

## Quick links

- **Already using v0.1 on Omarchy?** Start with [`OPENCODE_UPGRADE.md`](OPENCODE_UPGRADE.md) and preserve existing data/configuration.
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
- Private local dashboard port `5056`, plus distinct public-only read-only preview port `5057` serving **only** `/`, `/index.html`, and `/data.json`. Private API and health details are never copied into the public payload.
- Telegram: `/progress`, `/status`, `/meme`, `/meme GATO`, `/health`, `/news`, `/alerts`, `/link`, `/help`. Explicit allowlist, no anonymous access, push alerts only to the operator. Bot polling runs in a thread independent of market collection.
- Static `site/` directory and `python -m afterburner export-public` to publish a manually refreshed snapshot via the included GitHub Actions Pages workflow.

## Omarchy installation

The author's destination is `/mnt/jormungandr-rw/AFTERBURNER`, but the standalone installer now supports alternate paths for friends. First-time install:

```bash
cd /mnt/jormungandr-rw/AFTERBURNER
bash ops/install_omarchy.sh
~/.local/share/afterburner/venv/bin/python -m afterburner doctor
systemctl --user enable --now afterburner.service
```

The installer creates an isolated Python venv under `~/.local/share/afterburner/venv`, SQLite at `~/.local/state/afterburner/afterburner.db`, and a separate `~/.config/systemd/user/afterburner.service` unit. Credentials are configured in `~/.config/afterburner/.env` (mode `600`) and never committed. Do not reuse JAFAR tokens or state. The user service starts neither the Telegram bot nor alerts until a valid bot token and allowed chat ID are configured.

For upgrades, **do not blindly overwrite the existing `config/*.yaml`** or the current SQLite database. Use `OPENCODE_UPGRADE.md`.

## CLI

```bash
PY="$HOME/.local/share/afterburner/venv/bin/python"
$PY -m afterburner doctor        # static checks; no network
$PY -m afterburner progress      # collector and freshness summary
$PY -m afterburner status        # service heartbeat
$PY -m afterburner once          # one live market/news pass; service MUST be stopped
$PY -m afterburner bot-chats     # discover allowed chat IDs; service MUST be stopped
$PY -m afterburner export-public # write site/data.json from SQLite; no network
```

To publish on GitHub Pages, commit and push the sanitized `site/data.json` to your own public repository, then select **Settings → Pages → Source: GitHub Actions**. Pages does **not** run Python and cannot supply live data unless an external publisher updates the repository. For a live shareable URL while Omarchy is online, use Funnel on **public preview port 5057 only**.

## Testing

```bash
python -m unittest discover -s tests -v
python -m compileall -q afterburner
```

Tests are deterministic and offline: they check market parsing, scoring gates, Telegram command access control and public-site data isolation. Live BingX/DEX APIs, Telegram delivery, Omarchy systemd and public tunnel behavior **must be smoke-tested on the user's host**.

## Security and research constraints

1. **Never tunnel `:5056`.** It exposes private source diagnostics; share only the sanitized public view on `:5057`.
2. **Never commit secrets** — `.env`, private home configuration, SQLite, logs, API tokens or chat IDs. The public static exporter uses an explicit allowlist of fields.
3. **Unknown Telegram chats are ignored.** Friends can only query the status bot if their chat IDs are explicitly added; they do not automatically receive push alerts.
4. **Coin tickers are not proof of mint identity.** The default `mint_verified: false` must not be changed without credible, per-market evidence. A token-matched DEX pool is not automatically the underlying asset of a futures product.
5. **News isn't price direction.** Gaming headlines are discovery evidence, not automatically positive signals. Social-platform metrics are not fabricated; X/Telegram social listening isn't included.
6. **No trading capability.** AFTERBURNER has no Binance/BingX private API calls, trade buttons, leverage actions, order approvals or execution module.
7. **Sunset is simple:** `systemctl --user disable --now afterburner.service`; shut down Funnel separately; archive the independent SQLite database if desired. JAFAR is unaffected.
