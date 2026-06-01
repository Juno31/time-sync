# Objectives

## Primary goal
A single locally-run application that:
1. Controls video recording on multiple iPhones from a browser-based control UI (start/stop/arm).
2. Produces **time-synchronizable** recordings (top priority) for downstream 2D pose estimation and 3D reconstruction.
3. Uploads recorded clips to the host computer running the app.
4. Lets the user change iPhone recording configuration (resolution, fps, bitrate, camera, etc.) from the web UI.
5. Lets the user set recording metadata (session title, subject, trial, notes) from the web UI.

## Scope / non-goals
- **In scope:** capture + sync infrastructure, control UI, config UI, metadata, upload, post-hoc sync tool, HTML report.
- **Out of scope (for now):** the pose-estimation and 3D-reconstruction algorithms themselves. The output is
  synchronized, well-documented clips + a frame-alignment mapping that those downstream tools consume.
- **Out of scope:** native iOS app (user chose web client). Architecture leaves a clean seam to add one later.

## Connection method (decided)
- **WiFi LAN** is the only viable transport (video bandwidth + control + sync). Bluetooth rejected (bandwidth/timing).
- **QR code** used only for device pairing (encodes capture-page URL + session token).

## Success criteria
- N (>=4) iPhones connect over LAN and appear live in the control UI.
- A single "Start" triggers all phones to record; "Stop" ends and triggers upload.
- All clips + per-frame timestamp sidecars + metadata land on the host disk under one session folder.
- The post-hoc sync tool outputs a per-camera frame-offset mapping and aligned clips.
- **Measured** inter-camera sync error is characterized (ms, distribution) against a ground-truth visual event,
  and documented in the HTML report — not assumed.
