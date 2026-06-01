#!/usr/bin/env bash
# Generate a locally-trusted TLS cert for HTTPS on the LAN (required by iOS for getUserMedia).
# Uses mkcert. Install once: `brew install mkcert nss` then `mkcert -install`.
# On each iPhone you must also install + fully trust the mkcert root CA (see README/notes).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs

LAN_IP="$(python3 - <<'PY'
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
try: s.connect(("8.8.8.8",80)); print(s.getsockname()[0])
except Exception: print("127.0.0.1")
finally: s.close()
PY
)"

echo "Generating cert for: localhost 127.0.0.1 ${LAN_IP}"
if command -v mkcert >/dev/null 2>&1; then
  mkcert -cert-file certs/cert.pem -key-file certs/key.pem localhost 127.0.0.1 "${LAN_IP}"
  echo "Done. mkcert root CA must be installed & trusted on each iPhone:"
  echo "  1) AirDrop/email the file printed by:  mkcert -CAROOT  (rootCA.pem)"
  echo "  2) iPhone: Settings > General > VPN & Device Management > install profile"
  echo "  3) iPhone: Settings > General > About > Certificate Trust Settings > enable full trust"
else
  echo "mkcert not found; generating a self-signed cert with openssl (browser will warn)."
  openssl req -x509 -newkey rsa:2048 -nodes -keyout certs/key.pem -out certs/cert.pem \
    -days 825 -subj "/CN=${LAN_IP}" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:${LAN_IP}"
fi
