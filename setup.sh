#!/usr/bin/env bash
# One-shot environment setup for macOS / Linux.
#   bash setup.sh
# Creates a virtualenv, installs Python deps, and checks the external tools
# (ffmpeg, mkcert) the app needs. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

echo "== camera time sync :: setup =="

# 1. Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+ from https://www.python.org/downloads/"
  exit 1
fi
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo "[1/3] Python $PYV detected."
python3 -c 'import sys; sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)' || {
  echo "ERROR: Python 3.10+ required (found $PYV)."; exit 1; }

# 2. venv + dependencies
echo "[2/3] Creating .venv and installing dependencies…"
python3 -m venv .venv
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt
echo "      Python dependencies installed."

# 3. external tools (not pip-installable)
echo "[3/3] Checking external tools…"
if command -v ffmpeg >/dev/null 2>&1; then echo "      ffmpeg  OK"; else
  echo "      ffmpeg  MISSING — required for sync/reports."
  echo "              macOS:  brew install ffmpeg"
  echo "              Linux:  sudo apt install ffmpeg"
fi
if command -v mkcert >/dev/null 2>&1; then echo "      mkcert  OK"; else
  echo "      mkcert  MISSING — required for HTTPS (iOS camera). One-time:"
  echo "              macOS:  brew install mkcert nss && mkcert -install"
  echo "              Linux:  see https://github.com/FiloSottile/mkcert#installation"
fi

echo
echo "Setup complete. Next:"
echo "  1) (one-time) trust the mkcert root CA on each iPhone — see README."
echo "  2) start the host:   ./start.command   (or ./run.sh)"
