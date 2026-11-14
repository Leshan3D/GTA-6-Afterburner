# OpenCode mission — deploy AFTERBURNER onto your host

You are OpenCode, acting as the local Linux implementation operator. ChatGPT is responsible for the architecture and supplied the complete v0.2.0 implementation in `AFTERBURNER-v0.2.0.zip`. Your role is to install, inspect, smoke-test, report failures and apply only necessary platform/endpoint compatibility fixes. **Do not redesign this into another project.** Do not modify any existing project's directory, executable, systemd unit, environment, stored snapshots, Telegram bot or dashboard.

## Authorized destination

Your AFTERBURNER project directory (e.g. `<YOUR_PROJECT_DIR>`)

This directory exists on your host but is **not mounted in ChatGPT's code execution environment**. The archive must be transferred to your host first. Locate it using your Downloads path or ask where it was saved. Never assert an archive path without checking.

## Implementation sequence

1. **Inspect before any writes:** Check your target directory. If it already has content, stop for a backup/overwrite decision. Do not touch other projects.
2. **Inspect package:** `tar -tzf /path/to/AFTERBURNER-v0.2.0.zip | head -40`. Check it has a top-level `AFTERBURNER/` directory and no suspicious paths. Create destination if missing.
3. **Extract code:** `mkdir -p <YOUR_PROJECT_DIR> && tar -xzf /path/to/AFTERBURNER-v0.2.0.zip -C <YOUR_PROJECT_DIR> --strip-components=1`. Never extract into another project's directory.
4. **Install independently:** `cd <YOUR_PROJECT_DIR> && bash ops/install_omarchy.sh`. If Python version is incompatible, use an existing tested Python 3.11+ such as an available mise installation via `AFTERBURNER_PYTHON=...`. The installer creates a *new* venv under `~/.local/share/afterburner/venv` and state in `~/.local/state/afterburner` to avoid running venv/SQLite from a possibly NTFS mount. It creates `~/.config/systemd/user/afterburner.service` but doesn't start it.
5. **Run independent offline tests and one live read-only market/news poll:**

   ```bash
   cd <YOUR_PROJECT_DIR>
   PY="$HOME/.local/share/afterburner/venv/bin/python"
   "$PY" -m unittest discover -s tests -v
   "$PY" -m afterburner doctor
   "$PY" -m afterburner once
   "$PY" -m afterburner status
   ```

6. **Validate API responses and investigate data gaps.** Verify `GATO-USDT`, `DACITY-USDT`, `CYBERLEEK-USDT`, `GTA6-USDT` exist on BingX public API; check public endpoint schemas. Fix parser incompatibilities only where demonstrated by real API output. Add regression tests for any change. Verify 5m candle timing, OI base units and order-book quantity semantics. Check DEX Screener `baseToken.address` matching. Do not invent numbers or blindly mark identity verified. `candidate_mint` fields are unverified against BingX underlyings until independent trustworthy evidence is found and documented.
7. **Review risk gates and reporting.** Zero fake sentiment signals. `RESEARCH_ONLY` with identity defects, no trade-ready statuses. Telegram must be optional and use *independent* bot credentials from `~/.config/afterburner/.env` only. Do not add trading/order endpoints or paste exchange API keys.
8. **Enable service only after smoke checks succeed:**

   ```bash
   systemctl --user enable --now afterburner.service
   systemctl --user status afterburner.service --no-pager
   curl -fsS http://127.0.0.1:5056/healthz
   curl -fsS http://127.0.0.1:5057/api/assets | head -c 1000
   journalctl --user -u afterburner.service -n 80 --no-pager
   ```

9. **Report back to user** with actual service status, local dashboard URL, whether all four BingX symbols are supported, which mint verifications remain incomplete, which sources failed, sample healthy data timestamps, test totals and exact files changed. If blocked, leave service off and report the blocker. Do not silently modify other projects to get it running.

## Nonnegotiable boundaries

- Do not read or reuse other projects' `.env`, bot token, runtime DB, Python venv or state files.
- Do not change other projects' services, dashboards, or ports.
- AFTERBURNER uses loopback port **5056** and is **read-only/alert-only**.
- No new trading permissions, no auto-trading. No secret values in logs or commit.
- Graceful stop: `systemctl --user disable --now afterburner.service`.

## Source and functional reference

- `README.md` explains the design and limitations.
- `docs/DEPLOYMENT.md` supplies operational and acceptance checks.
- `docs/DATA_SOURCES.md` lists BingX and DEX Screener public API paths.
- `config/watchlist.yaml`, `config/settings.yaml`, `config/sources.yaml` are the only initial per-narrative registry/config files.
- `tests/` contains offline mocks. Production APIs must also be validated on your host.

## Final outcome

Deliver a functioning local read-only `http://127.0.0.1:5056/` dashboard and optional separate Telegram watch alerts with 4 contract cards; preserve other projects entirely. If any market data or identity can't be verified, display a blocking flag instead of improvising.
