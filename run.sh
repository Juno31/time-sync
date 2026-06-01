#!/usr/bin/env bash
# Single launch command for the live host app.
#   ./run.sh                 # start the host + open the control UI
#   ./run.sh --port 9000     # any app.py flags are passed through
set -euo pipefail
cd "$(dirname "$0")"

# venv (created on first run)
if [ ! -d .venv ]; then
  echo "[run] creating .venv and installing deps…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  # full requirements: aiohttp+segno serve the app; numpy/scipy/opencv power the
  # per-recording sync reports (/report). ffmpeg must be on PATH separately.
  ./.venv/bin/python -m pip install --quiet -r requirements.txt
fi
PY=./.venv/bin/python

# Ensure analysis deps are present (older .venvs had only aiohttp+segno, so the
# auto sync/report pipeline failed with "No module named 'numpy'"). Cheap import
# check on each launch; only installs when something is actually missing.
if ! "$PY" -c "import aiohttp, segno, numpy, scipy, cv2" >/dev/null 2>&1; then
  echo "[run] installing/updating Python dependencies…"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
fi
command -v ffmpeg >/dev/null 2>&1 || echo "[run] WARNING: ffmpeg not found on PATH — sync/reports need it (brew install ffmpeg)."

# Stop any previous instance first. A stale server still bound to the port is the
# usual reason a "restart" doesn't take effect — the browser keeps hitting old code.
PORT=8443
prev=""; for a in "$@"; do
  if [ "$prev" = "--port" ]; then PORT="$a"; fi; prev="$a"
done
if command -v lsof >/dev/null 2>&1; then
  STALE="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
  if [ -n "$STALE" ]; then
    echo "[run] stopping previous instance on port $PORT (pids: $STALE)…"
    echo "$STALE" | xargs kill 2>/dev/null || true
    sleep 1
  fi
fi

# cert check (iOS needs HTTPS for the camera)
if [ ! -f certs/cert.pem ] || [ ! -f certs/key.pem ]; then
  echo "[run] No TLS cert found. iOS will NOT allow the camera over HTTP."
  echo "[run] Generate one now with:  bash scripts/setup_certs.sh"
  echo "[run] Starting over HTTP anyway (fine for localhost testing)…"
fi

exec "$PY" app.py --open "$@"
