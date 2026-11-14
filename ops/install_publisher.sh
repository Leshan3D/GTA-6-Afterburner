#!/usr/bin/env bash
# Installs only the independent AFTERBURNER GitHub Pages publisher timer.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$HOME/.local/share/afterburner/venv/bin/python"
[[ -x "$PYTHON" && -f "$ROOT/afterburner/public.py" ]] || { echo "Install the AFTERBURNER engine first" >&2; exit 2; }
[[ "$ROOT" != *JAFAR* ]] || { echo "Refusing JAFAR path" >&2; exit 2; }
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/state/afterburner/publisher"
UNIT="$HOME/.config/systemd/user/afterburner-publisher.service"
TIMER="$HOME/.config/systemd/user/afterburner-publisher.timer"
cat > "$UNIT" <<EOF
[Unit]
Description=AFTERBURNER public GitHub Pages snapshot publisher
After=network-online.target
Wants=network-online.target
ConditionPathIsDirectory=$ROOT

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYTHON -m afterburner.publisher
TimeoutStartSec=240
NoNewPrivileges=true
UMask=0077
EOF
cat > "$TIMER" <<'EOF'
[Unit]
Description=Publish AFTERBURNER public market snapshot every 10 minutes

[Timer]
OnBootSec=3min
OnUnitInactiveSec=10min
Persistent=true
Unit=afterburner-publisher.service

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
printf 'Publisher installed, but not activated. First run:\n  %s -m afterburner.publisher --dry-run\nThen enable:\n  systemctl --user enable --now afterburner-publisher.timer\n' "$PYTHON"
