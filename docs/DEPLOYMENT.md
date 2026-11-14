# Deployment validation

## Before installation

1. Ensure the archive is present on your host. Inspect with `tar -tzf /path/to/AFTERBURNER-v0.2.0.zip | head`. Do not guess file paths.
2. Inspect your target directory **before extracting**. If there are already files, back them up and confirm instead of overwriting.
3. Create the target and extract the archive:
   ```bash
   tar -xzf /path/to/AFTERBURNER-v0.2.0.zip -C <YOUR_PROJECT_DIR> --strip-components=1
   cd <YOUR_PROJECT_DIR>
   ```
4. `bash ops/install_omarchy.sh`
5. If `python3` is wrong/missing, use an existing tested Python 3.11+ such as an available mise installation via `AFTERBURNER_PYTHON=... bash ops/install_omarchy.sh`. Do not reuse other projects' virtual environments.

## Smoke tests — must report actual observations

```bash
cd <YOUR_PROJECT_DIR>
PY="$HOME/.local/share/afterburner/venv/bin/python"
"$PY" -m unittest discover -s tests -v
"$PY" -m afterburner doctor
"$PY" -m afterburner once
"$PY" -m afterburner status
```

Observe API output and inspect `~/.local/state/afterburner/afterburner.db` via the app CLI / Python if needed. Verify `contract_active` for all four symbols and examine `source_health`: warnings may reflect API changes, unsupported contracts, denied feeds, or empty klines; do **not** convert missing data to bullish recommendations. Confirm public BingX klines format and order-book quantity semantics with current API docs.

Check two separate facts for each token: (a) the BingX perp symbol is active, and (b) the DEX candidate mint corresponds to the asset implied by the futures listing. Do not set `mint_verified: true` solely because a token with that ticker appears on DEX Screener. Use independent, trustworthy evidence, then record the evidence in `config/watchlist.yaml`. If verification is impossible, leave the gate on and report it.

## Start as service only when installation and smoke checks pass

```bash
systemctl --user enable --now afterburner.service
systemctl --user status afterburner.service --no-pager
curl -fsS http://127.0.0.1:5056/healthz
curl -fsS http://127.0.0.1:5057/data.json | head -c 1200
journalctl --user -u afterburner.service -n 100 --no-pager
```

Dashboard: `http://127.0.0.1:5056/`. **Do not publish the HTTP port externally.** Telegram alerts require `AFTERBURNER_TELEGRAM_BOT_TOKEN` and `AFTERBURNER_TELEGRAM_CHAT_ID` in the independent `~/.config/afterburner/.env`. Leave blank if not configured.

## Rollback

```bash
systemctl --user disable --now afterburner.service
# Data remains in ~/.local/state/afterburner/ for later audit.
```

## Acceptance criteria

- All offline tests pass and service runs after host reboot (subject to the mount being available).
- `/healthz` returns 200 and dashboard displays the four assets.
- Each BingX contract independently reports active / unavailable, never invented live data.
- DEX values show only matching-base-address pools and identity verification remains conservative.
- Freshness, spread, insufficient order book, and missing identity block verified setup state.
- Two consecutive market polls generate non-null OI deltas when the API supplies valid data.
- No exchange files, units, ports, environment variables or credentials have been touched outside AFTERBURNER's own directories.
- Report any feeds blocked by the host and provide logs without leaking tokens.
