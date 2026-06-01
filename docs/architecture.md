# Architecture

## Stack (minimal by design)
Goal: the smallest infra that still satisfies the hard constraints. No framework, no database, no build step, no cloud.

- **Host process:** a single Python script `app.py` using **`aiohttp`** (one third-party dependency). It does all
  three irreducible jobs in one process: (a) serve the HTTPS pages, (b) run the WebSocket hub for control +
  clock-sync, (c) accept streamed uploads and write them to disk. Python because the user is Python/ML and
  downstream pose tools (opencap-core/Pose2Sim) are Python.
- **Why a host process is unavoidable (not bloat):** iOS Safari requires HTTPS for `getUserMedia` even on LAN;
  a browser cannot write to the host filesystem (uploads need a receiver); a synchronized trigger needs a shared
  low-latency reference. See decisions.md.
- **Transport:** HTTPS + secure WebSocket over the local WiFi network.
- **Phone client:** plain web page (PWA-capable) in iOS Safari, **vanilla HTML/CSS/JS, no framework/bundler**.
  `getUserMedia` + `MediaRecorder` for capture, `requestVideoFrameCallback` for per-frame timestamp logging.
- **Control UI:** vanilla web page served by `app.py` (desktop browser on the host or any LAN device).
- **Storage:** filesystem only — session folders + JSON sidecars. **No database.**
- **Post-hoc sync tool:** a separate offline Python script `sync.py` (OpenCV + numpy/scipy). Not part of the
  running app; run after collection.
- **Local TLS:** mkcert (or a generated self-signed CA) trusted on each iPhone — the one unavoidable extra,
  forced by iOS's secure-context requirement.

## Components
1. **Pairing** — control UI renders a QR (capture URL + session token); phone scans, opens capture page over HTTPS.
2. **Device registry + control channel** — each phone holds a WebSocket; server tracks connected devices,
   broadcasts arm/start/stop/config, detects disconnects.
3. **Clock-sync service** — NTP-like RTT ping/pong; estimates per-device clock offset + uncertainty.
4. **Capture client** — applies config, previews, records, logs per-frame timestamps, shows the visual sync beacon.
5. **Synchronized trigger** — server schedules start at a shared future time T0 using synced clocks; fires beacon.
6. **Upload service** — chunked/resumable POST of clip + timestamp sidecar + metadata to the host disk.
7. **Config UI** — resolution, requested fps, bitrate, codec/container, camera facing, torch (if available).
8. **Metadata UI** — session title, subject id, trial name, notes, tags; written as sidecar JSON; filename templating.
9. **Live preview** — phones send throttled low-res JPEG frames (~3 fps) over the WS hub; the control UI
   shows a per-camera monitor grid. Gated on/off by control presence so phones don't waste uplink. Kept
   deliberately light so it never starves `MediaRecorder` during a recording.
10. **Post-hoc sync** (`sync.py`) — audio-clap cross-correlation + per-frame-timestamp offset ->
    frame-offset map (`offsets.json`) + aligned/resampled CFR clips (`sync/aligned/`).
11. **HTML procedure report** — step execution, validation results, errors, sync-accuracy plots.

## Data flow
control UI --(arm/config/start)--> server --(WS broadcast)--> phones
phones --(record locally, log frame timestamps + beacon)--> on stop --(chunked upload)--> server -> disk
disk session folder --> post-hoc sync tool --> aligned clips + frame map --> HTML report

## Repo layout (planned, minimal)
```
camera time sync/
  app.py             single host process: HTTPS static serving + WS hub + upload-to-disk (aiohttp)
  run.sh             single launch command (venv bootstrap + cert check + app.py --open)
  sync.py            offline post-hoc sync (audio-clap x-corr + timestamps + ffmpeg align)
  calibrate.py       intrinsics (checkerboard) + extrinsics (shared board | reference object/manual picker)
  report.py          generate report/procedure_report.html (steps, exec log, sync SVG plots, error log)
  web/
    control/         control UI (vanilla HTML/CSS/JS) — roster, config, metadata, preview grid
    capture/         phone capture page (vanilla HTML/CSS/JS)
  tests/             smoke.py (host), test_sync.py, test_calib.py, run_all.sh (orchestrator)
  sessions/          recorded sessions land here (gitignored)
  certs/             local TLS cert/key (gitignored)
  scripts/           cert setup (mkcert)
  requirements.txt   aiohttp + segno (live app); numpy/scipy/opencv (sync + calibrate)
  docs/              planning + notes (this folder)
  report/            generated HTML procedure report
```

## Session folder layout (on disk)
```
sessions/<session_id>/
  session.json                 metadata (title, subject, trial, config, devices, T0)
  <device_label>/
    video.mp4                  recorded clip
    frames.json                per-frame display timestamps mapped to server clock
    clock.json                 RTT samples + estimated offset/uncertainty for this device
  sync/
    offsets.json               per-camera frame offsets (post-hoc result)
    aligned/                   resampled, aligned clips at common fps
```
