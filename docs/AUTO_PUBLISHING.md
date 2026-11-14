# Automatic public dashboard publishing (v0.3.0)

AFTERBURNER is a **read-only** market research engine. GitHub Pages is a static website.
This optional publisher exports a strictly allowlisted JSON market snapshot from the local SQLite database every ~10 minutes and pushes **only `site/data.json`** to the configured public repository. GitHub Actions then deploys the site.

## Operating model

- Research backend: private, local `127.0.0.1:5056`.
- Sanitized public preview: local `127.0.0.1:5057`.
- GitHub Pages: `https://leshan3d.github.io/GTA-6-Afterburner/`.
- Publisher: separate Git checkout under `~/.local/state/afterburner/publisher/publisher-repo`, never the active source checkout.
- Snapshot/export state: `~/.local/state/afterburner/publisher/publisher-status.json`.
- The `site/index.html` browser checks the deployed snapshot once a minute and computes **freshness from actual market observation times**. Collection continues every three minutes on the host, but the published site may lag by the scheduled interval plus deployment time.

## Installation on Omarchy

From `/mnt/jormungandr-rw/AFTERBURNER`:

```bash
bash ops/install_publisher.sh
# Run unit tests first
~/.local/share/afterburner/venv/bin/python -m unittest discover -s tests -v
# Test exporter and git authentication in the separate checkout; no push
~/.local/share/afterburner/venv/bin/python -m afterburner.publisher --dry-run
# Enable publishing after confirming dry run and Git permissions
systemctl --user enable --now afterburner-publisher.timer
systemctl --user list-timers --all | grep afterburner
```

Requires `git` and **noninteractive GitHub push access** for the logged-in Linux user. Use GitHub CLI, credential manager or SSH authentication configured locally. Never put a token in a remote URL, environment file, repository or status output. If the remote uses SSH, verify separately that the pinned HTTPS clone works under the available credential helper before enabling the timer.

## Observability and controls

```bash
~/.local/share/afterburner/venv/bin/python -m afterburner.publisher --status
systemctl --user start afterburner-publisher.service  # manually run once
journalctl --user -u afterburner-publisher.service -n 60 --no-pager
systemctl --user disable --now afterburner-publisher.timer
```

Telegram `/publish` reports the last attempt, push and observed market sample, but **does not** assert GitHub Actions deployment succeeded. Confirm deployment independently in the GitHub Actions UI.

## Failure semantics

- Missing market observations: fail, do not generate a fake live timestamp.
- Unchanged market/news data: skip commit and push; the browser will eventually mark the last snapshot stale.
- Missing GitHub authentication/network: fail, preserve private source checkout.
- Concurrent run: exit without starting a second publisher.
- Push conflict: fail without a force push; next attempt fetches `origin/main` in the isolated checkout.
- Existing unrelated working changes in publisher checkout: fail safely, request operator review.

GitHub Pages cannot stream live updates; this is scheduled snapshot publication. Never open the private dashboard to the public internet.
