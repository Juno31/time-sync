# Decision log

Records *why* choices were made, so future sessions don't relitigate them.

## 2026-05-31 — initial architecture
- **Client = web/PWA in iOS Safari.** User's explicit choice. Trade-off accepted: no per-frame hardware
  timestamps, no fps lock, recording stops on screen lock. => accuracy shifted to post-hoc sync layer.
- **Transport = WiFi LAN (HTTPS + WSS).** Bluetooth rejected: insufficient bandwidth for video, worse timing.
- **Pairing = QR code** (capture URL + session token) — convenience only, not a transport.
- **Server = Python + FastAPI.** User is a Python/ML researcher; downstream pose estimation stays in-stack.
- **Sync = layered.** Recording-time best-effort (clock offset + soft trigger + visual beacon + frame log)
  PLUS post-hoc (beacon detection + keypoint-velocity cross-correlation). Post-hoc carries accuracy.
  Mirrors OpenCap, which is native yet still syncs in post.
- **Scale target = 4+ cameras, high fps.** All streams treated as VFR; resampled to common timeline in post.
- **Architecture keeps the client behind a clean seam** so a native iOS recorder can be added later for
  true recording-time sync without changing the server.

## 2026-05-31 — model on OpenCap; feed its local pipeline
- Project explicitly references the **OpenCap** service (PLOS Comput Biol 2023, Q1; Apache-2.0). We mirror its
  proven pattern: 2+ phones record independently, checkerboard calibration, post-hoc sync via keypoint-velocity
  cross-correlation, DLT triangulation -> OpenSim. See docs/opencap_reference.md.
- **Added a calibration step** (Step 7b) — was a gap; mandatory for 3D triangulation.
- **Step 8 implements OpenCap's exact cross-correlation sync**, but anchored first by our visual beacon
  (motion-free hard reference OpenCap lacks; protects low-motion trials).
- **Output targets opencap-core's local pipeline** ("videos collected synchronously from another source"),
  so the user can run opencap-core / opencap-processing / Pose2Sim for the actual 3D reconstruction.
- Scope boundary: we build capture + sync + calibration only; we do **not** reimplement triangulation/biomech.

## 2026-05-31 — minimal stack (user: "no useless backend server")
- **Decision:** smallest infra that still works. One Python script `app.py` (aiohttp, single dependency) for
  HTTPS serving + WebSocket hub + upload-to-disk; vanilla HTML/JS UIs (no framework/bundler); filesystem only
  (no DB); offline `sync.py` for post-hoc sync. Dropped FastAPI+uvicorn.
- **Why a host process is still required (not removable):**
  1. iOS Safari requires HTTPS for `getUserMedia` even on LAN — something must serve a trusted-cert page.
  2. A browser cannot write to the host filesystem — uploads need a receiving process to save clips to disk.
  3. Synchronized trigger + clock-offset estimation need a shared low-latency reference; a tiny WS hub is the
     minimal primitive. (WebRTC peer-to-peer would still need a signaling server = more infra.)
- **How to apply:** keep dependencies to aiohttp (+ opencv/numpy/scipy for sync only). Resist adding frameworks,
  databases, or a JS build step. mkcert is the one unavoidable extra (iOS secure-context).

## Open items for user confirmation
- Approve this plan before coding begins (Step 0).
- Confirm: do you have an Apple-ID-based way to keep Safari foreground/awake during multi-minute trials,
  or are trials short (<~1 min)? Affects whether Wake Lock alone is sufficient.
