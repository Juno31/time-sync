# Running the app (macOS host + iPhone)

## 0. One-time setup
```bash
cd ~/Desktop/"camera time sync"
python3 -m venv .venv && source .venv/bin/activate
pip install aiohttp segno          # live app only needs these two
```

## 1. Local HTTPS cert (required — iOS blocks camera without HTTPS)
```bash
brew install mkcert nss            # if not already installed
mkcert -install                    # trusts the local CA on the Mac
bash scripts/setup_certs.sh        # writes certs/cert.pem + certs/key.pem for your LAN IP
```

## 2. Trust the cert on the iPhone (one-time)
1. On the Mac: `mkcert -CAROOT` → open that folder → AirDrop **rootCA.pem** to the iPhone.
2. iPhone: tap the file → **Install profile** (Settings → Profile Downloaded → Install).
3. iPhone: **Settings → General → VPN & Device Management** → install the mkcert profile.
4. iPhone: **Settings → General → About → Certificate Trust Settings** → enable **full trust** for mkcert.

## 3. Run — no terminal needed
**Double-click `start.command`** in Finder. It creates the venv on first run, starts the host, opens the
control UI, and **auto-creates/refreshes the TLS cert if your Wi-Fi IP changed** (no need to re-run
`setup_certs.sh`). If macOS asks to allow incoming connections, **Allow**.

(First time only: right-click `start.command` → Open, to clear macOS Gatekeeper. Advanced/terminal:
`./run.sh` or `python app.py` still work.)

The control UI header shows host status: 🔒 HTTPS + your LAN IP, or an ⚠ warning if running over HTTP.

## Tests (no hardware needed)
```bash
bash tests/run_all.sh              # host smoke (15/15) + offline sync validation (2/2)
python tests/test_calib.py         # calibration geometry validation (5/5)
```

## After recording: the sync report is in the UI
In the control UI, the **"Recordings & sync reports"** panel lists every session. Click **View report**
(or **Build report** the first time) — it opens a per-recording page with the inter-camera sync error vs
the frame period and the clap-detection diagnostic. No terminal step; the host runs `sync.py` for you and
serves `sessions/<id>/report.html`. Use **rebuild** to regenerate after re-recording.

### Advanced / terminal (optional)
```bash
python sync.py sessions/<id> --fps 30 --verify          # clap-aligned CFR clips -> sessions/<id>/sync/
python session_report.py sessions/<id> --force          # rebuild the per-recording report
# calibration (for 3D triangulation; needs a printed checkerboard):
python calibrate.py intrinsics       --session sessions/<id> --cols 9 --rows 6 --square 0.025
python calibrate.py extrinsics-board --session sessions/<id> --cols 9 --rows 6 --square 0.025
python report.py --session sessions/<id>                # regenerate the overall procedure report
```

## 4. Use it
- Mac browser: open the **control URL** (e.g. `https://<LAN-IP>:8443/`). No warning (cert trusted).
- iPhone on the **same Wi-Fi**: open Camera app, scan the **QR** in the control UI → opens the capture page →
  **Allow camera & microphone**. The phone appears in the "Cameras" table with a live clock offset.
- Set capture config → **Push config to all**. Fill in session **title / subject / trial / notes**.
- **Start (synchronized)** → countdown → recording begins → at the **CLAP** prompt, clap once → **Stop**.
- Files upload to: `~/Desktop/camera time sync/sessions/<session_id>/<label>/`
  (`video.mp4`, `frames.json`, `clock.json`, plus `session.json` at the session root).

## Troubleshooting
- **Phone can't reach the Mac / QR page won't load:** you're likely on a network with client isolation
  (common on campus/eduroam/guest Wi-Fi). Fix: use a home router or the **iPhone's Personal Hotspot**
  (connect the Mac to the iPhone hotspot), then re-run `setup_certs.sh` (LAN IP changes) and restart `app.py`.
- **"camera blocked" on the phone:** the cert isn't fully trusted (redo step 2) or you opened over `http`.
- **Recording stopped early:** keep the capture page foreground and the screen on. Wake Lock is requested
  automatically; for 1–2 min trials this is sufficient.
- **Single iPhone:** everything works end-to-end, but 3D triangulation needs ≥2 cameras (add phones later).
```
