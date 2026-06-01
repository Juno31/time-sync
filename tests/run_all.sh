#!/usr/bin/env bash
# Consolidated end-to-end smoke test (no hardware needed).
#   1. start app.py over HTTP on a test port (forces no-cert HTTP)
#   2. run the host-side smoke checks (health, pairing/QR, WS registry, clock, trigger,
#      config, preview on/relay, session metadata, chunked+hashed upload)
#   3. run the offline sync.py validation on synthetic data
# Exits non-zero if anything fails.
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8088}"
PY="$(command -v python3)"
[ -x ./.venv/bin/python ] && PY=./.venv/bin/python

echo "=== starting host on :$PORT (HTTP) ==="
"$PY" app.py --port "$PORT" --certs /tmp/__nocerts__ >/tmp/cts_app.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 1 15); do sleep 1; curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; done

echo "=== host-side smoke ==="
SMOKE_BASE="http://127.0.0.1:$PORT" "$PY" tests/smoke.py; S1=$?

echo "=== offline sync validation ==="
"$PY" tests/test_sync.py; S2=$?

echo "=== summary ==="
[ $S1 -eq 0 ] && echo "smoke: PASS" || echo "smoke: FAIL"
[ $S2 -eq 0 ] && echo "sync:  PASS" || echo "sync:  FAIL"
[ $S1 -eq 0 ] && [ $S2 -eq 0 ]
