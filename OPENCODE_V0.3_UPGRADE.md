# OpenCode local implementation handoff — AFTERBURNER v0.3.0

**This package was developed by ChatGPT against the locally available v0.2.0 release.** The operator verified that their actual working repository `/mnt/jormungandr-rw/AFTERBURNER` and `origin/main` matched at commit `c69bfe5`. Before applying, inspect this verified baseline and examine the patch to reconcile any differences, especially the sanitized docs. Do not blindly overwrite other changes.

## Outcome

- Local/public dashboard shows freshness metrics: market health, newest market observation, last public snapshot generated, planned 10-minute publication cadence.
- New `afterburner/publisher.py` publishes **only public JSON** via dedicated isolated Git clone and verifies strict allowlist schema.
- `ops/install_publisher.sh` installs a **disabled by default** user service and timer for 10-minute publishing.
- Telegram `/publish` reports accurate local publisher status; no claim of verified Pages deployment.
- JAFAR remains completely untouched. No trades, API keys, tunnels or private dashboard exposure.

## Deployment instructions

1. In the existing AFTERBURNER source, confirm `git status --short` is clean and `git rev-parse HEAD` is `c69bfe5`. Take a backup or create a safety branch.
2. Inspect the supplied `AFTERBURNER-v0.3.0.patch`; use `git apply --check` before applying it. It was generated against a v0.2.0 source archive rather than a direct clone of `c69bfe5`; if necessary, reconcile changes manually without overwriting the running config or other modifications. **Do not copy the whole release archive over the source tree.**
3. Apply changes on a feature branch (e.g. `feat/auto-pages-publisher`).
4. Run `python -m unittest discover -s tests -v`, `python -m compileall -q afterburner`, and `python -m afterburner doctor` in the established venv. Inspect HTML at private port 5056 and public port 5057. The public UI reads market data from `/data.json` without exposing localhost to GitHub Pages.
5. Verify git credential helper/SSH has authorized, noninteractive push access to `https://github.com/Leshan3D/GTA-6-Afterburner`, and do not commit any credentials.
6. Run `bash ops/install_publisher.sh`. It installs units but does not enable the timer. Verify `python -m afterburner.publisher --dry-run` works without pushing.
7. Merge feature changes into local `main` and push source with operator approval. Then enable `systemctl --user enable --now afterburner-publisher.timer` and inspect logs.
8. Check actual `site/data.json` commit on GitHub, Pages Actions deployment result, and public website for the latest observed market time; verify each matches the local sample within expected publication/deployment delay.
9. Test Telegram `/publish`. Confirm it distinguishes GitHub push from confirmed Pages deployment.
10. Record the final commit, test results, timer status, deployed site URL, timestamps and any outstanding issues. Never alter JAFAR, delete SQLite history or open port 5056.

If code fails locally, fix the demonstrated bug with a regression test, document the change, and report architectural deviations for review.
