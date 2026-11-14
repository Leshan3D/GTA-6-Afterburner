#!/usr/bin/env bash
# Standalone, non-destructive AFTERBURNER Omarchy installation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Portable across friends' Linux machines. The operator's primary target is
# /mnt/jormungandr-rw/AFTERBURNER, but other install paths are supported.
if [[ "$ROOT" == *"/JAFAR-ENGINE" || "$ROOT" == *"/PERSONAL JAFAR ENGINE"* ]]; then
  echo "ERROR: Refusing to install inside a JAFAR directory" >&2
  exit 2
fi
if [[ ! -f "$ROOT/afterburner/service.py" || ! -f "$ROOT/config/settings.yaml" ]]; then
  echo "ERROR: this does not look like the AFTERBURNER source directory" >&2
  exit 2
fi
PYTHON="${AFTERBURNER_PYTHON:-$(command -v python3)}"
"$PYTHON" - <<'PY'
import sys
assert sys.version_info >= (3,11), 'Python 3.11+ required'
PY
# Python runtime and SQLite live on Linux-native home, not the mounted NTFS disk.
# This avoids mount noexec quirks, reduces filesystem write contention, and is
# independent from JAFAR's environment.
VENV="$HOME/.local/share/afterburner/venv"
STATE="$HOME/.local/state/afterburner"
UNIT="$HOME/.config/systemd/user/afterburner.service"
mkdir -p "$(dirname "$VENV")" "$STATE" "$(dirname "$UNIT")"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/ops/requirements.txt"
cd "$ROOT"
export AFTERBURNER_DATA_DIR="$STATE"
"$VENV/bin/python" -m unittest discover -s tests -v
"$VENV/bin/python" -m afterburner doctor
# Secrets live on Linux-native home, not the (possibly NTFS) code mount.
ENV_DIR="$HOME/.config/afterburner"
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"
if [[ ! -f "$ENV_DIR/.env" ]]; then
  cp "$ROOT/.env.example" "$ENV_DIR/.env"
  chmod 600 "$ENV_DIR/.env"
fi
cat > "$UNIT" <<SERVICE
[Unit]
Description=AFTERBURNER standalone GTA narrative intelligence (READ ONLY)
After=network-online.target
Wants=network-online.target
ConditionPathIsDirectory=$ROOT

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
Environment=AFTERBURNER_DATA_DIR=$STATE
ExecStart=$VENV/bin/python -m afterburner serve
Restart=on-failure
RestartSec=12
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=default.target
SERVICE
systemctl --user daemon-reload
cat <<DONE
AFTERBURNER installed and tests passed. Service is not started automatically.
Service: $UNIT
State: $STATE
Private dashboard: http://127.0.0.1:5056/
Public-safe preview: http://127.0.0.1:5057/ (when enabled)
Next (after API smoke test): systemctl --user enable --now afterburner.service
JAFAR not touched.
DONE
