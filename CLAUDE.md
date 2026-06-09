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
- [x] Per-recording report: session_report.py -> sessions/<id>/report.html (sync verdict vs frame period,
      clap-detection diagnostic as inline SVG, audio-vs-timestamp cross-check). Served via GET /report.
- [x] Minimal-terminal (2026-06-01): app auto-creates/refreshes the mkcert cert on LAN IP change;
      start.command double-clickable; control UI header shows host status (HTTPS + LAN IP); run.sh kills any
      stale instance on the port and self-heals missing deps (numpy/scipy/cv2) on launch.
- [x] 2026-06-02 batch: auto sync+report when all cameras finish uploading (toggle in UI); per-session
      Sync/Report buttons + POST /sync; /media static route + synchronized side-by-side video player in the
      report with a playback cursor on the clap waveform; target fps now read from session config (not fixed 30);
      session folders named title_subject_trial; "Sync residual" column in the recordings panel. Smoke 20/20.
- [x] Repo prepared for GitHub (README, LICENSE MIT, setup.sh/ps1, hardened .gitignore). Remote:
      https://github.com/Juno31/time-sync.git (user pushes; sandbox cannot push — no creds, mount blocks git).
- [ ] Calibration on real footage (Step 7b): needs a printed checkerboard (user deferred). reproj error TBD.

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

## Possible upgrades (backlog) — recorded 2026-06-02

Prioritized ideas surfaced during development. Each notes **why** and **how/where**. None are started.

### Accuracy / methodology
- [ ] **True windowed Pearson cross-correlation** in `sync.py:xcorr_lag`. Now: normalized cross-correlation
      (NCC) on globally standardized envelopes; the "confidence" ≈ Pearson r only at full overlap (it divides by
      global L2 norms, not the per-lag overlap-window norms). Upgrade: re-center/re-scale within each lag's
      overlap window (or via FFT running sums) so confidence is a rigorous r at every lag. Sharper peak selection.
- [ ] **Sub-frame alignment.** Render currently resamples to CFR at the target fps, so residual is quantized to
      the frame grid (≈½-frame floor). Keep fractional offsets / render at a higher common fps, or phase-shift,
      to push residual below one frame even at 60 fps.
- [ ] **Independent ground-truth residual.** Clap-only residual is partly self-confirming (measured on the same
      signal used to align). Add an optional on-screen ms-timer / LED visual event option and measure residual on
      that. This is also the PLAN Step 8 validation rig. See docs/sync_design.md "Validation".
- [ ] **Keypoint-velocity refinement (OpenCap Layer B).** Documented plug-in point in sync.py; not implemented.
      RTMPose/MediaPipe → Butterworth low-pass → cross-correlate summed vertical keypoint speed for sub-frame.

### Robustness / UX
- [ ] **Multi-clap auto-warning.** Detect ambiguous claps (ratio of primary vs secondary xcorr peak, or
      argmax-vs-xcorr disagreement > 1 frame) and flag it in the report/UI so the user re-records a clean clap.
- [ ] **Auto-sync edge case.** If a participant camera disconnects before reporting `uploaded`, maybe_autosync
      never fires (it waits for all participants). Add a timeout / "finalize now" button / detect upload completion
      server-side from the final chunk rather than only the WS status message.
- [ ] **Host version indicator.** We repeatedly hit confusion where a stale host process served old code. Add a
      build/version string to GET /health and have the control UI warn if the running host predates the page.
- [ ] **frames.json fidelity.** rVFC logs *display* callbacks, not encoded capture frames, so eff_fps is a proxy
      and can read absurdly high (e.g. 233). Consider deriving true capture fps differently, or label it clearly.

### Calibration / 3D (needs hardware)
- [ ] **Step 7b on real footage** + in-UI "calibration capture mode" button (records intrinsics+extrinsics clips,
      runs calibrate.py server-side, shows reproj error). Target < ~1 px. Then hand off to opencap-core/Pose2Sim.

### Docs / packaging
- [ ] **Refresh docs/app_documentation.html** — it predates /sync, /media, the auto-process flow, configured-fps,
      and the residual column. Update the routes table and the report description.
- [ ] **Cross-platform host parity** (Windows/Linux launcher equivalents of start.command/run.sh).
- [ ] Decide whether to keep CLAUDE.md / docs/error_log.md in the public repo (internal-flavored notes).
