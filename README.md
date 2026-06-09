# camera time sync

Local, self-hosted app to record **time-synchronized video across multiple iPhones** from a browser, upload
the clips to a host computer, and align them in post for 2D pose estimation / 3D reconstruction.

The phones are ordinary iPhones running **Safari** (no native app to install). A small process on your
computer serves a control UI and a phone capture page over your local network, coordinates a synchronized
start and a shared clap, and aligns the recordings afterward. The approach mirrors
[OpenCap](https://www.opencap.ai/) (PLOS Comput Biol 2023): phones record independently and real
synchronization is recovered in **post-processing** — necessary because iOS Safari cannot expose per-frame
hardware timestamps, cannot lock frame rate, and stops recording on screen lock.

---

## Features

- **Browser-only phone client** — pair an iPhone by scanning a QR code in Safari; no App Store, no profiles
  beyond a one-time local cert.
- **Synchronized trigger** — the host arms all phones to start at a shared future timestamp and prompts a clap.
- **Automatic sync + report on upload** — when every camera that recorded a session finishes uploading, the
  host runs the time-sync and builds that session's report automatically (toggleable). Or trigger it manually
  per recording with **Sync** / **Report** buttons.
- **Rich per-recording report** — inter-camera sync error vs the frame period, a **synchronized side-by-side
  player** of the aligned clips, the clap-detection diagnostic (audio envelopes + cross-correlation curve with
  a live playback cursor), a capture table, and an independent audio-vs-timestamp cross-check — all in the browser.
- **Post-hoc alignment** — audio-clap cross-correlation (primary) + per-frame timestamps (cross-check),
  resampled to a common constant frame rate (**your configured capture fps**) with ffmpeg.
- **Readable session folders** — named from your inputs as `title_subject_trial` (falls back to a timestamp).
- **OpenCap-compatible output** — for downstream 3D reconstruction in
  [opencap-core](https://github.com/stanfordnmbl/opencap-core) / [Pose2Sim](https://github.com/perfanalytics/pose2sim).
- **Minimal stack** — one aiohttp process, vanilla HTML/JS, filesystem only. No database, framework, or build step.

---

## Requirements

| Tool | Why | Install |
|---|---|---|
| **Python 3.10+** | host process + analysis | <https://www.python.org/downloads/> |
| **ffmpeg / ffprobe** | audio extraction + VFR→CFR resampling | macOS `brew install ffmpeg` · Linux `apt install ffmpeg` |
| **mkcert** | locally-trusted HTTPS cert (iOS requires HTTPS for the camera) | macOS `brew install mkcert nss` |
| iPhone(s) | the cameras | iOS Safari, on the **same Wi-Fi** as the host |

Host OS: **macOS is the primary target** (the launcher and cert flow are macOS-oriented). The host process
itself also runs on Linux/Windows. **Windows users: see [README-Windows.md](README-Windows.md)** for
Windows setup, the `run.ps1` launcher, certificate trust without AirDrop, and firewall notes.

---

## Install

```bash
git clone https://github.com/Juno31/time-sync.git
cd time-sync
bash setup.sh          # creates .venv, installs Python deps, checks ffmpeg/mkcert
```

`setup.sh` is safe to re-run. On Windows use `powershell -ExecutionPolicy Bypass -File setup.ps1`.

### One-time HTTPS cert (required — iOS blocks the camera over HTTP)

```bash
mkcert -install                 # trust the local CA on this computer
```

Then trust the same CA on **each iPhone** (one-time per phone):

1. On the Mac run `mkcert -CAROOT` and AirDrop the `rootCA.pem` from that folder to the iPhone.
2. iPhone: tap the file → install the profile (*Settings → Profile Downloaded → Install*).
3. iPhone: *Settings → General → VPN & Device Management* → install the mkcert profile.
4. iPhone: *Settings → General → About → Certificate Trust Settings* → enable **full trust** for mkcert.

The host **auto-creates and refreshes** the TLS cert for your current LAN IP on startup, so you do not need
to regenerate it manually when your network changes.

### iPhone capture settings — required for 60 fps

> **⚠️ Important:** On **each** iPhone, turn **off Auto FPS** before recording, or iOS will silently drop the
> camera to 30 fps in lower light and your 60 fps target won't be met.
>
> **Settings → Camera → Record Video → Auto FPS → Off**
>
> (On some iOS versions this appears as choosing a fixed rate such as "60 fps" instead of an "Auto" option.)
> Also record in adequate lighting — even with Auto FPS off, very low light can reduce the effective frame rate.

---

## Run

Double-click **`start.command`** in Finder (first time: right-click → Open to clear Gatekeeper), or:

```bash
./run.sh                        # starts the host and opens the control UI
```

The control UI opens at `https://<LAN-IP>:8443/`. Its header shows host status (🔒 HTTPS + LAN IP).

### Record a trial

1. On each iPhone (same Wi-Fi), open Safari and **scan the QR** in the control UI → **Allow camera & mic**.
   The phone appears in the **Cameras & live preview** panel with a live clock offset and a ~3 fps monitor.
2. Set capture config → **Push config to all**. Fill in the session **Title / Subject / Trial** (these name the
   folder, e.g. `gait_pilot_joonho_1`) and any notes.
3. **Start (synchronized)** → at the **CLAP** prompt, clap once (sharp, single impulse) → **Stop**.
4. Clips upload automatically to `sessions/<title_subject_trial>/<camera>/`.

### The sync report

With **Auto-sync & build report after upload** enabled (default), the report is built automatically once all
cameras finish uploading. Otherwise, in the **Recordings & sync reports** panel, click **Sync** then **Report**
(or just **Report**, which syncs on demand). The report includes a synchronized side-by-side player — press
Play and motion should line up across cameras — plus the residual-vs-frame-period verdict and the clap diagnostic.

> Tip: for the cleanest result, record **one isolated clap** with ~1 s of silence on either side. Multiple
> claps still align correctly (cross-correlation uses the whole pattern) but make the diagnostic noisier.
> Note the verdict is judged against your configured fps — at 60 fps one frame is 16.7 ms, a stricter bar than 30 fps.

---

## How it works

The shared time base is the host wall-clock (ms). Phones estimate their offset to it via an NTP-like
ping/pong over WebSocket. `MediaRecorder` output is variable-frame-rate, so `sync.py` recovers the true
inter-camera offset offline:

1. **Audio clap cross-correlation (primary)** — the single physical clap recorded by every phone's mic gives
   a sharp, sub-frame relative offset (uses the whole envelope pattern, so it is robust to echoes/multiple claps).
2. **Per-frame timestamps (independent cross-check / fallback)** — first server-clock stamp per camera from
   `frames.json`.
3. **Resample** — ffmpeg renders each clip to a common constant frame rate (your configured fps) with the claps coincident.
4. **Verify** — re-measures the residual inter-camera offset on the aligned clips (the headline accuracy number).

See [`docs/sync_design.md`](docs/sync_design.md) for the exact algorithm, constants, and the
clap-position-vs-offset distinction.

Full design notes are in [`docs/`](docs/) and the rendered [`docs/app_documentation.html`](docs/app_documentation.html).

---

## Project structure

```
app.py                 aiohttp host: HTTPS pages + WebSocket hub + chunked upload
sync.py                offline post-hoc synchronization (clap x-corr + timestamps + ffmpeg)
calibrate.py           OpenCap-style camera calibration (checkerboard / reference object)
session_report.py      per-recording HTML report (sync verdict + clap diagnostic, inline SVG)
report.py              overall procedure report across all build steps
run.sh / start.command launcher, macOS/Linux (venv bootstrap + open UI)
run.ps1                launcher, Windows (PowerShell equivalent of run.sh)
setup.sh / setup.ps1   environment setup (bash / PowerShell)
README-Windows.md      Windows host guide (setup, cert trust, firewall)
requirements.txt       Python dependencies
web/control/           control UI (browser, on the host)
web/capture/           phone capture page (iOS Safari)
scripts/setup_certs.sh manual cert generation (usually unnecessary — host auto-refreshes)
tests/                 smoke.py (host), test_sync.py, test_calib.py, run_all.sh
docs/                  objectives, architecture, sync design, plan, decisions, running, error log
sessions/              recordings land here (git-ignored)
```

---

## Tests

No hardware needed:

```bash
bash tests/run_all.sh          # host smoke (20/20) + offline sync validation (2/2)
python tests/test_calib.py     # calibration geometry validation (5/5)
```

---

## Troubleshooting

- **Phones can't reach the host / QR page won't load** — campus / eduroam / guest Wi-Fi often blocks
  device-to-device traffic (client isolation). Use a home router, or turn on one iPhone's **Personal Hotspot**
  and connect the Mac + the other phone to it.
- **"Camera blocked" on the phone** — the mkcert cert isn't fully trusted (redo the trust steps) or the page
  was opened over `http`.
- **Recording stopped early** — keep the capture page in the foreground; a Wake Lock is requested
  automatically but iOS can still interrupt long sessions.
- **Report says "report engine unavailable"** — install the analysis deps on the host: `bash setup.sh` (or
  `pip install -r requirements.txt`) and ensure `ffmpeg` is on PATH.

---

## Acknowledgements

Design and methodology reference [OpenCap](https://www.opencap.ai/) (Apache-2.0), with prior art also from
[Pose2Sim](https://github.com/perfanalytics/pose2sim) and Google's
[libsoftwaresync](https://github.com/google-research/libsoftwaresync). This project is independent and not
affiliated with those teams.

## License

[MIT](LICENSE) © 2026 Joonho
