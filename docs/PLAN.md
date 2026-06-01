# Implementation plan

Each step has a validation gate. On pass -> proceed automatically. On fail -> append to
docs/error_log.md and re-run the step with an updated procedure. After all steps pass ->
generate the HTML procedure report.

Legend for validation: **A** = automated/programmatic, **D** = requires a real iPhone on the LAN.

---

### Step 0 — Project scaffold & environment (minimal)
Set up repo structure, Python venv, **single `app.py` (aiohttp)** with a `/health` route; `requirements.txt`
= aiohttp. No framework, no DB, no build step.
- **Validation (A):** `python app.py` starts; `GET /health` returns 200; repo layout matches architecture.md.

### Step 1 — Local HTTPS + LAN reachability + pairing QR
Generate a trusted local cert (mkcert); serve over HTTPS; control UI renders a QR (capture URL + token).
- **Validation (D):** scanning the QR on an iPhone opens the capture page over HTTPS with **no cert error**,
  `getUserMedia` is permitted, and a live camera preview shows. Confirm on >=1 real device.
- **Fallback if fail:** document cert-install steps for iOS; retry until secure context is granted.

### Step 2 — Device registry + WebSocket control channel
Phones connect via WS and register (id, label). Control UI lists devices live; broadcast arm/start/stop.
- **Validation (D):** connect N phones; all appear in UI; "ping all" round-trips; pulling a phone offline
  is detected within a few seconds.

### Step 3 — Clock-synchronization layer
NTP-like RTT offset estimation per device; show offset + jitter in UI.
- **Validation (A+D):** offset estimate is stable (report median RTT and offset stddev). Cross-check:
  two phones side-by-side both render server-time; photograph; visible difference within reported bound.

### Step 4 — Web-client recording + per-frame timestamps + beacon
Capture page records via MediaRecorder, logs per-frame timestamps (`requestVideoFrameCallback`) mapped to
server clock, shows visual sync beacon; server-scheduled synchronized soft trigger at shared T0.
- **Validation (A+D):** short clip saved on each phone; `frames.json` present and monotonic; beacon frame
  detectable; effective fps mean/variance reported (confirms VFR handling). Wake Lock keeps screen awake.

### Step 5 — Upload to host computer
Chunked/resumable upload of clip + sidecars + metadata; saved under `sessions/<id>/<device>/`.
- **Validation (A):** uploaded file size/hash matches source; all N devices' files present; a simulated
  mid-upload interruption resumes and completes intact.

### Step 6 — Recording configuration UI
UI controls resolution, requested fps, bitrate, codec/container, camera facing, torch; pushed before arming.
- **Validation (A):** a setting changed in the UI is reflected in the actual recorded file
  (verify resolution/bitrate with `ffprobe`).

### Step 7 — Metadata & session-info UI
Form: session title, subject id, trial name, notes, tags. Written to `session.json`; filename templating.
- **Validation (A):** `session.json` matches UI inputs; filenames follow the template; reopening a session
  restores its info.

### Step 7b — Camera calibration capture (OpenCap-style)
Film a **printed checkerboard** with all cameras; recover per-camera **intrinsics + extrinsics**
(`cv2.findChessboardCorners` + `calibrateCamera` / `solvePnP`, as OpenCap does). Store under the session.
Required for 3D triangulation; without it the synced clips cannot be reconstructed in 3D.
- **Validation (A+D):** corners detected in all views; reprojection error reported (target < ~1 px);
  `calibration.json` written per camera. Mirrors OpenCap's checkerboard step (see opencap_reference.md).
- **STATUS (2026-05-31): built (`calibrate.py`).** Subcommands: `intrinsics` (checkerboard),
  `extrinsics-board` (shared board, solvePnP — PRIMARY), `extrinsics-object` (reference object / known 3D
  control points with interactive 2D point-picker or precomputed JSON — ALTERNATIVE). Geometry validated on
  synthetic ground truth (tests/test_calib.py 5/5: intrinsics exact, extrinsic reproj 0.0 px). Real-footage
  reprojection error to be characterized on device.

### Step 8 — Post-hoc synchronization module (OpenCap method + beacon)
Ingest a session and align cameras to a common timeline. Implements OpenCap's exact sync, anchored by our beacon:
1. **Beacon coarse alignment:** detect the synchronized flash / decode the on-screen ms timestamp per camera
   -> robust integer-frame offset (motion-free; works even on low-motion trials, which OpenCap can't handle alone).
2. **Keypoint-velocity cross-correlation refinement (OpenCap):** run a fast 2D pose estimate; Butterworth
   low-pass + confidence-threshold the keypoints; for gait, cross-correlate L/R ankle image-plane speeds
   (accept delay 0.1–1 s); else use the max cross-correlation of the **summed vertical speed of all keypoints**
   between camera pairs. Refine to sub-frame.
3. **Output:** `offsets.json` (per-camera frame offset) + `aligned/` clips resampled to a common fps, in an
   **opencap-core-compatible layout** so the user can run opencap-core / Pose2Sim for DLT triangulation + OpenSim.
- **Validation (A+D) — CRITICAL:** record a common visual event (ms timer / LED flash) on all cameras;
  after sync, measure residual inter-camera offset of that event; report the **sync-error distribution (ms)**
  vs frame period. This is the headline result.
- **STATUS (2026-05-31): built + validated on synthetic ground truth.** `sync.py` implements audio-clap
  cross-correlation (primary) + per-frame-timestamp offset (prior/fallback); ffmpeg VFR->CFR resample +
  aligned start; `--verify` re-measures residual. Synthetic 2-cam test: true +300 ms recovered exactly,
  residual 0.0 ms. Keypoint-velocity refinement (OpenCap) left as an optional plug-in. Real-footage residual
  to be characterized on device (needs the actual clap recording).

### Step 9 — Integrate as one local app
Single launch (server + open control UI + cert check). Smoke-test script for end-to-end run.
- **Validation (A):** fresh run from clean state passes the smoke test (server up, UI loads, WS connects).

### Step 10 — HTML procedure report
Generate the visualized HTML report: per-step execution, validation outcomes, errors from error_log.md,
and sync-accuracy plots.
- **Validation (A):** report opens in a browser and contains every step + its result + the sync plots.
