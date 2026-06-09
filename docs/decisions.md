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

## 2026-06-09 — Capture fps default 1280×720@60 + auto-config on connect; wake-lock hardening

**Symptom (user):** real sessions captured ~30 fps despite the config requesting 60
(`pilot_joonho_1`, `test_tom_1`: 33.0 ms median frame interval = true 30 fps).

**Cause:** the request was 1920×1080. iOS Safari exposes a 60 fps camera *format* only at
≤720p; at 1080p the back camera's only format is 30 fps, so the `frameRate:{ideal:60}` hint is
silently dropped. Long-standing WebKit limitation — bug 179994 ("can not control framerate &
resolution using getUserMedia"). `frameRate.ideal` is advisory per W3C mediacapture (MDN).

**Decisions:**
- Default capture config is now **1280×720 @60**, the only iOS combination that yields 60 fps.
  Set in three places: capture `startCamera()` defaults, control-UI inputs, and `Hub.last_config`.
- Capture constraint changed `frameRate:{ideal}` → `frameRate:{min:min(30,fps), ideal:fps}` to bias
  iOS toward a high-fps format. (Avoided `{exact}` — throws OverconstrainedError and kills the stream.)
- **Auto-config on connect:** host pushes `Hub.last_config` to each camera immediately after
  `registered`, so a phone configures itself with no manual "Push config to all". A pushed config
  updates `Hub.last_config`, so later joiners inherit it. (app.py register handler + config handler.)
- Capture page now shows a **`cam WxH@fps` pill** (green only if achieved fps ≥ requested−2) so the
  negotiated rate is visible on the phone — the only ground truth, since iOS varies by device/thermal.
- Control UI: added a Preset dropdown (720p@60 primary / 1080p@30) + corrected the muted help text.

**Wake lock ("Permission was denied" on iPhone 16 Pro):** root cause is iOS refusing Screen Wake
Lock in **Low Power Mode** (returns NotAllowedError). Code also requested the lock on page load
(before any gesture) and never re-acquired after iOS auto-releases it on tab-hide. Fixes: request on
first `pointerdown` and on `visibilitychange→visible`; on NotAllowedError, log guidance to disable
Low Power Mode. (Not a code bug we can fully solve — LPM denial is OS policy; surfaced to the user.)

**Validation:** `tests/run_all.sh` → smoke **21/21** (incl. new "camera auto-configured on connect"
check asserting 1280×720@60) + sync **2/2**. On-device gate (pending real re-record): capture pill
reads `1280x720@60` and `frames.json` median Δt ≈ 16–17 ms (≈60 fps) instead of 33 ms.

## 2026-06-09 — Sync render must use accurate seek; clips conformed to equal frame count

**Bug:** `sync.py:render_aligned` used `ffmpeg -ss <start> -i` (input seek before `-i`), which snaps
the trim to the nearest keyframe and shifted the clap by ~one frame — the source of the recurring,
suspiciously-constant ~22 ms residual (identical at 30 and 60 fps). Verified by ffmpeg A/B test:
input-seek → −21 ms residual; accurate seek (`-ss` after `-i`) → 0.0 ms. **Decision:** always place
`-ss` after `-i` for frame-accurate trimming, accepting the slightly slower decode (clips are short).
Also force equal frame counts (`-frames:v round(dur*fps)` + post-render stream-copy conform to the
common minimum), so frame *i* is the same instant across cameras for downstream triangulation.
See docs/error_log.md 2026-06-09 for the full diagnostic. Validated: residual 0.0 ms on real
test_Joonho_1, both clips 561 frames; smoke 26/26 + sync 2/2.

## Open items for user confirmation
- Approve this plan before coding begins (Step 0).
- Confirm: do you have an Apple-ID-based way to keep Safari foreground/awake during multi-minute trials,
  or are trials short (<~1 min)? Affects whether Wake Lock alone is sufficient.
