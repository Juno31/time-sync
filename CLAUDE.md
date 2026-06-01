# CLAUDE.md — camera time sync

Local-running app to control time-synced video recording across multiple iPhones (web/PWA client),
upload clips to the host PC, and synchronize them in post for pose estimation / 3D reconstruction.

## Project context notes (read these for full context)

- [docs/objectives.md](docs/objectives.md) — goals, scope, non-goals, success criteria
- [docs/architecture.md](docs/architecture.md) — stack, components, data flow, repo layout
- [docs/sync_design.md](docs/sync_design.md) — the time-sync methodology, platform constraints, sources
- [docs/opencap_reference.md](docs/opencap_reference.md) — OpenCap's real design; what we borrow / change; opencap-core compatibility
- [docs/PLAN.md](docs/PLAN.md) — step-by-step plan, each with a validation gate
- [docs/decisions.md](docs/decisions.md) — decision log / history (why, not just what)
- [docs/error_log.md](docs/error_log.md) — failure logs (appended when a step's validation fails)
- [docs/running.md](docs/running.md) — how to run the app (macOS host + iPhone), cert setup, troubleshooting

## Hard constraints (do not forget)

- **Client = web/PWA in iOS Safari** (user's choice; no native iOS app).
- iOS Safari **cannot** expose per-frame hardware capture timestamps, **cannot** lock fps,
  and **stops recording on screen lock**. `MediaRecorder` output is variable-frame-rate.
  => Real sync accuracy must come from the **post-hoc layer**, not recording-time. This is also
     how OpenCap (native app) actually syncs. See docs/sync_design.md.
- `getUserMedia` requires a **secure context (HTTPS)** on iOS even over LAN. A trusted local
  cert must be installed on each iPhone.
- Target: **4+ cameras, high fps**. Treat all streams as VFR; resample to a common timeline in post.
- **Minimal stack** (user: no useless backend). One `app.py` (aiohttp) = HTTPS + WS hub + upload-to-disk;
  vanilla HTML/JS UIs; filesystem only, no DB, no framework, no build step; offline `sync.py`. A small host
  process is irreducible (iOS HTTPS requirement, save-to-disk needs a receiver, synchronized trigger). See decisions.md.

## Working behavior in this project (from project instructions)

1. Plan before coding; reference prior projects (done: OpenCap, Pose2Sim, caliscope, libsoftwaresync).
2. Each step has a pre-set validation method. Proceed automatically when validation passes.
3. On validation failure: append an entry to docs/error_log.md, then re-run the step with an updated procedure.
4. After all steps pass: produce a **visualized HTML procedure report** (execution, errors, sync-accuracy results).
5. Fact-based, concise. No unsourced claims. Research papers Q1–Q2 only.

## Status

- [x] Prior-art research
- [x] Architecture decided + constraints documented
- [x] Plan approved by user
- [x] Live host app built: app.py (aiohttp) + web/control + web/capture + scripts/setup_certs.sh
- [x] Host-side validated by tests/smoke.py (13/13): health, pairing/QR, WS registry+roster,
      clock ping/pong, synchronized-trigger broadcast, config push, session metadata, chunked+hashed upload
- [x] Live phone preview (throttled JPEG over WS, viewer-gated): relay validated (on/relay/off)
- [x] Step 8 post-hoc sync built: sync.py (audio-clap x-corr + timestamp offset + ffmpeg align);
      validated on synthetic 2-cam data (true +300 ms recovered, residual 0.0 ms)
- [x] Step 9 integration: run.sh single launch (--open) + tests/run_all.sh (host 15/15 + sync 2/2)
- [x] Step 10 HTML report: report.py -> report/procedure_report.html (steps, exec log, sync SVG plots, error log)
- [x] Step 7b calibration: calibrate.py — checkerboard intrinsics + extrinsics (shared board PRIMARY,
      reference-object/manual point-picking ALTERNATIVE); geometry validated on synthetic (5/5)
- [x] Step 1 & 4 VALIDATED ON DEVICE (2026-06-01): real 2-iPhone session 20260601-205418-ba9f04 over HTTPS;
      frames.json monotonic, clap present, effective fps reported.
- [x] Step 8 on REAL FOOTAGE (2026-06-01): synchronized trigger ~8 ms; post-alignment residual −22 ms
      (≈0.66 frame @30fps, within one frame). Audio x-corr robust to a multi-clap take; see error_log.
- [x] Per-recording report (NEW): session_report.py -> sessions/<id>/report.html (sync verdict vs frame
      period + clap-detection diagnostic as inline SVG). Served in-UI via GET /report; listed in the control
      UI "Recordings & sync reports" panel. Smoke now 18/18 (adds /sessions + /report checks).
- [x] Minimal-terminal (user req 2026-06-01): app auto-creates/refreshes the mkcert cert when the LAN IP
      changes (no setup_certs.sh re-run); start.command is double-clickable; control UI header shows host
      status (HTTPS + LAN IP); reports built/viewed entirely in the browser.
- [ ] Calibration on real footage (Step 7b): needs a printed checkerboard (user deferred). reproj error TBD.
- [ ] Optional: keypoint-velocity refinement in sync.py; "calibration capture mode" button in the app UI

## Pending tasks (resume next session)

Host-side + sync validated (smoke 18/18, sync 2/2, calib 5/5 synthetic). Steps 1/4/8 now also validated on
real 2-iPhone footage. Per-recording reporting is built and served in the UI. Remaining work is calibration
on real footage (needs a checkerboard) and optional refinements.

### 🕐 Calibration on real footage (Step 7b) — Deferred by user (2026-06-01)
**Why:** required for 3D triangulation; user chose to skip until a printed checkerboard is available.
**User directions:** shared checkerboard = primary; reference object / manual point-picking = alternative.
**Current state:** `calibrate.py` validated on synthetic (5/5). No real reproj error yet.
**Next steps (UI-first; avoid terminal):**
- [ ] Add a "calibration capture mode" button to the control UI that records short intrinsics + extrinsics
      clips per camera into the session, then triggers `calibrate.py` server-side and shows reproj error in-UI.
- [ ] Target reprojection error < ~1 px; fold into the per-session report.

### 🕐 Optional enhancements (deferred, no hardware needed) — Not Started (2026-05-31)
**Why:** robustness + UX; only pursue if the clap proves insufficient or capture-mode UX is wanted.
**Current state:** sync.py has a documented optional plug-in point for keypoints; app UI has no calibration mode.
**Next steps:**
- [ ] Wire a 2D pose backend (RTMPose/MediaPipe) into `sync.py` for OpenCap keypoint-velocity cross-correlation as a secondary refinement.
- [ ] Add a "calibration capture mode" button to the control UI (records short intrinsics + extrinsics clips per camera into the session).
