# Sharing AFTERBURNER beyond localhost (v0.2)

**Two different goals require two different publication paths.** Both share only a sanitized public research dashboard, never the private `:5056` dashboard or JAFAR. The public view is local on `http://127.0.0.1:5057/` and exposes only `/` and `/data.json`.

## Option A — live, free public link (Tailscale Funnel)

Tailscale Funnel is currently beta, available on free plans and provides a public HTTPS URL. Your PC must be running AFTERBURNER and Funnel for the link to work. The machine's home IP address is not published via the link, but the public dashboard is visible to **anyone who has the link**.

1. Install/sign into Tailscale on Omarchy, enable MagicDNS + HTTPS certificates, and grant Funnel permission in your tailnet if prompted. Check `tailscale version` and `tailscale status`.
2. Confirm public-safe preview (not private dashboard):

   ```bash
   curl -f http://127.0.0.1:5057/
   curl -f http://127.0.0.1:5057/data.json
   curl -i http://127.0.0.1:5057/api/health  # must return 404
   ```

3. Start a shareable public URL:

   ```bash
   tailscale funnel 5057
   ```

   Copy the `https://...ts.net` address printed by Tailscale; do not invent a URL. `Ctrl+C` ends this foreground sharing session. Review the live `tailscale funnel` documentation before making it a persistent background service. No router port forwarding is required.

**Important:** Only route `5057` to the outside world. Never route `5056` (private dashboard), your SQLite database, Git directories, systemd services, or any JAFAR port. Stop Funnel to withdraw the public view. Public sharing does not provide an authentication layer for the displayed research data.

## Option B — free GitHub Pages public snapshot

GitHub Pages serves static HTML, CSS, JS and JSON. It does **not** run Python, SQLite, Telegram polling or the BingX/DEX collectors. This repository includes `site/index.html`, the offline sanitized snapshot exporter, and a GitHub Actions Pages workflow.

With AFTERBURNER collecting on Omarchy, from the repo root run:

```bash
cd /mnt/jormungandr-rw/AFTERBURNER
~/.local/share/afterburner/venv/bin/python -m afterburner export-public
# ONLY sanitized files are written to site/: index.html, .nojekyll and data.json
# Inspect data.json before publishing it publicly.
python -m json.tool site/data.json | head -60

git add site/ .github/workflows/pages.yml
git commit -m "Publish public AFTERBURNER research snapshot"
git push origin main
```

On GitHub, go to the repo **Settings → Pages → Build and deployment → Source: GitHub Actions**, and enable the provided workflow. The Pages URL normally follows `https://USERNAME.github.io/REPOSITORY/`; verify the actual URL shown by GitHub rather than assuming it.

**Update cadence:** GitHub Pages shows the **last pushed** export. Even though its browser refreshes `data.json`, the GitHub-hosted numbers will not update until your Omarchy exporter runs and the changed `site/data.json` is committed and pushed. There is no automatic publishing token or background Git push in this version. A live Funnel URL is more appropriate for real-time sharing. Do not put exchange/Telegram credentials or database files in Git.

## Option C — temporary zero-domain link (Cloudflare Quick Tunnel)

For a quick demo, if `cloudflared` is installed:

```bash
cloudflared tunnel --url http://127.0.0.1:5057
```

The printed `*.trycloudflare.com` HTTPS URL changes between runs and has no uptime guarantee. This is a demo mechanism, not a permanent host. If you want only selected email addresses to view the demo, check Cloudflare Quick Tunnel's `--allowed-mail` option.

## Publication checklist

- `curl -i http://127.0.0.1:5057/api/health` is `404`; do not expose :5056.
- `site/data.json` has only public prices, research flags, history and headlines; no secrets, raw feed error strings or chat IDs.
- `.env`, `~/.config/afterburner/.env`, `~/.local/state/afterburner/*`, and the SQLite database are **never committed**.
- Futures/spot mint mappings are still unverified until evidenced; public view flags this clearly.
- Prices stale by >10 minutes are clearly marked as stale; exported Pages data inevitably becomes stale if not republished.
- Public availability is not guaranteed when your machine, Internet or the exchange APIs are down.

Official reference: https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site , https://tailscale.com/docs/features/tailscale-funnel , https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
