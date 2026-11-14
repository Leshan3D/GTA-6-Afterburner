# OpenCode handoff — AFTERBURNER v0.2 (Telegram + public preview)

**Role:** You are the local implementation operator. ChatGPT has provided the complete new code; you install, preserve state, run tests, smoke-test API compatibility, and report results. This is an **upgrade**, not a clean installation. Do NOT touch other local projects' files, services, Telegram bots, or any trading executor.

**Existing destination:** Your AFTERBURNER project directory (e.g. `<YOUR_PROJECT_DIR>`)

**Current project:** The user has a functional local `http://127.0.0.1:5056/` dashboard. Do NOT delete SQLite history or custom configuration. v0.2 adds a private whitelisted Telegram progress bot and a separate sanitized public dashboard `http://127.0.0.1:5057/`, with an optional static `site/` snapshot for GitHub Pages.

## Upgrade sequence

1. Locate the supplied `AFTERBURNER-v0.2.0.tar.gz` or `.zip` **on the user's machine**. Don't guess its Downloads path. Check archive contents for path traversal/symlinks before extracting. There is one `AFTERBURNER/` directory at the top.
2. Inspect current state before writes:

   ```bash
   systemctl --user status afterburner.service --no-pager
   ls -la <YOUR_PROJECT_DIR>
   git -C <YOUR_PROJECT_DIR> status --short 2>/dev/null || true
   ```

   If uncommitted local code modifications exist, preserve and report them; do not overwrite without reviewing the diff.
3. Stop **only** AFTERBURNER and make an **offline backup** in the operator's home directory. Preserve `~/.config/afterburner/.env`, `~/.local/state/afterburner/afterburner.db` and `config/*.yaml`. Do not copy credentials into the repository or upload them anywhere. Use SQLite's backup API or a consistent stopped-service copy; ensure the writer has exited. Do not touch other projects.
4. Extract archive into a **temporary staging directory**, not straight into the active destination, e.g. `mktemp -d` and `tar -xzf /actual/archive/path -C "$STAGING"`. Inspect the diff. Merge **new and changed source/docs/tests/site/.github** but preserve the existing `config/*.yaml` values and `.env`.
5. Merge only the new public config block into the existing `config/settings.yaml` (do not reset custom scheduling or gates):

   ```yaml
   public:
     enabled: true
     host: 127.0.0.1
     port: 5057
   ```

   Preserve the existing `server: port: 5056`. There must not be a binding to `0.0.0.0`.
6. Run:

   ```bash
   cd <YOUR_PROJECT_DIR>
   bash ops/install_omarchy.sh
   python -m unittest discover -s tests -v
   python -m compileall -q afterburner
   python -m afterburner doctor
   python -m afterburner progress
   python -m afterburner export-public
   ```

7. Inspect `site/data.json` for raw secrets before any commit. It is a deliberately sanitized public export. Tests must verify no private routes are exposed. Run read-only local preview smoke tests:

   ```bash
   systemctl --user start afterburner.service
   systemctl --user status afterburner.service --no-pager
   curl -fsS http://127.0.0.1:5056/healthz
   curl -fsS http://127.0.0.1:5057/data.json | head -c 750
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5057/api/health  # MUST be 404
   ```

8. **Telegram:** Ask the user to provide their own bot token *locally in `~/.config/afterburner/.env` only*, never in the report/chat or GitHub. Follow `docs/TELEGRAM.md`, including `bot-chats` with the service stopped to discover the primary chat ID, then restart. Send `/progress`, `/status`, `/meme GATO`, `/health`, `/news`, `/link` through Telegram; check all output. No auto-trading commands are allowed. Whitelisted friends only; do not set global public access to the bot.
9. **External sharing:** Follow `docs/PUBLIC_SHARING.md`. First verify port 5057 has no private endpoints, then obtain user approval before exposing it via Tailscale Funnel/Cloudflare tunnel. **NEVER expose 5056**, and never announce a link that wasn't actually printed by the tunnel or GitHub Pages settings.
10. **GitHub:** Inspect `git status` and `.gitignore` before publishing. Do NOT push to GitHub without the user's explicit authorization and repository destination. GitHub Actions workflow is included for static site deployment. Inform the user that it is a *published snapshot*, not a live hosted Python backend.

## Acceptance checklist

- Other local projects remain entirely unchanged and running independently.
- AFTERBURNER uses independent venv, state, env and ports 5056/private and 5057/sanitized-public.
- All 16+ offline tests pass, including Telegram recipient allowlist and public-route isolation.
- Telegram `/progress` correctly reports fresh/recorded token counts and news/market feed health; it never fabricates price data.
- Configured bot token and chat IDs never appear in public `site/data.json` or logs.
- Public preview only answers `/`, `/index.html`, `/data.json`; private routes return 404.
- No trading, leverage or authenticated exchange API credentials were introduced.
- `site/data.json` can be exported from the running SQLite dataset and prepared for user-approved GitHub Pages publication.
- Report actual live market feed status, exact code changes and preview links; no fabricated success claims.

**If something fails:** restore the stopped-service backup; leave other projects alone; report the failure with redacted logs. Do not redesign system architecture or modify the live exchange executor.
