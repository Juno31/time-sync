# Time-synchronization design

Sync is the project's top priority. This document records the methodology, the platform constraints
that force it, and the prior art it is based on.

## Platform constraints (iOS Safari web client)
These are documented limits of the chosen client, not design choices:
- A web page **cannot read per-frame hardware capture timestamps**. `requestVideoFrameCallback` gives
  *display*-side timing for a `<video>` element, not the camera's capture instant.
- fps **cannot be locked**; `getUserMedia` honors `frameRate` only as a hint, and auto-exposure lowers
  effective fps in low light. `MediaRecorder` output is therefore **variable-frame-rate (VFR)**.
- Recording **stops when the screen locks / tab backgrounds**. Keep screens awake (Wake Lock API where
  supported) and the page foregrounded for the whole capture.
- Camera access requires a **secure context (HTTPS)** even over LAN.

Implication: recording-time sub-frame sync is not reliably attainable on a web client. Accuracy must come
from the **post-hoc layer**. (OpenCap uses a *native* app and *still* syncs in post — see below.)

## Layered strategy

### Layer A — recording-time (best-effort, enables post alignment)
1. **Clock-offset estimation (NTP-like):** repeated WebSocket ping/pong; for each sample record
   t0 (client send), t1 (server recv), t2 (server send), t3 (client recv). RTT = (t3-t0)-(t2-t1);
   offset = ((t1-t0)+(t2-t3))/2. Keep the offset from the **minimum-RTT** samples and report jitter.
2. **Synchronized soft trigger:** server picks a shared start time T0 = now + Delta (e.g., 3 s) in
   server-clock terms; each phone converts to its local clock via its offset and starts at T0.
3. **Shared sync event (corrected):** a phone's own screen flash is NOT in its own camera view, so it cannot
   be a cross-camera reference. The shared reference must be an event *all cameras capture*:
   - **Audio clap (primary):** capture audio+video (`getUserMedia({audio:true,video:true})`). The server runs a
     synchronized countdown; the user produces ONE sharp clap. Every phone's mic records the same acoustic event;
     cross-correlating the audio tracks in post gives a sharp, sub-frame common reference.
   - **Displayed timecode board (optional):** a monitor/phone showing a rolling ms timestamp/QR placed in the
     capture volume, briefly filmed by all cameras — an optical hard reference (also the Step 8 validation rig).
4. **Per-frame timestamp log:** `requestVideoFrameCallback` logs each displayed frame's `mediaTime` /
   `expectedDisplayTime`, mapped to the server clock via the device offset. Stored as `frames.json`.

### Layer B — post-hoc (where accuracy is actually won)
1. **Shared-event detection:** detect the clap in each audio track (onset / audio cross-correlation) and/or the
   displayed timecode in pixels -> coarse per-camera offset, robust to clock error and independent of motion.
2. **Keypoint-velocity cross-correlation:** OpenCap/Pose2Sim method — run a fast 2D pose estimate,
   cross-correlate keypoint velocity (or vertical-motion) signals between camera pairs to refine to
   sub-frame, then resample all clips onto a common timeline at a fixed fps.
3. **Output:** `offsets.json` (per-camera frame offset) + `aligned/` resampled clips.

## Implemented algorithm (as built, `sync.py`)

This is exactly what the code does today, with constants. The headline accuracy comes from **audio-clap
cross-correlation**; per-frame timestamps are an independent cross-check / fallback. (Keypoint-velocity
refinement from Layer B is left as a documented optional plug-in and is **not** currently run.)

**Recording time (`app.py` + `web/capture`).** Each phone estimates its offset to the host clock with the
NTP-like ping/pong (offset taken from the minimum-RTT samples). On Start, the host schedules a shared
`t0_server_ms = now + 3000 ms` and a clap cue at `+5000 ms`; each phone converts those to its local clock and
begins recording at T0. During capture, `requestVideoFrameCallback` writes per-displayed-frame server-clock
stamps to `frames.json`. The user produces **one sharp clap** at the cue — that acoustic event, heard by every
phone's mic, is the cross-camera reference.

**Audio envelope (`extract_envelope`).** For each clip, ffmpeg extracts mono audio at 16 kHz. The signal is
rectified (absolute value), smoothed with a moving average of `SMOOTH_MS = 8 ms`, resampled to
`ENV_RATE = 1000 Hz`, then normalized to zero mean / unit standard deviation. The result is a 1 kHz amplitude
envelope where the clap is a sharp spike.

**Two independent offset estimates, per camera vs. a reference** (reference = `--ref` or the first camera):

1. *Audio cross-correlation* (`xcorr_lag`) — FFT full cross-correlation of the camera envelope against the
   reference envelope. The lag is `(argmax_index − (len(ref) − 1)) / 1000` seconds; a normalized confidence
   `peak / (‖cam‖·‖ref‖)` ∈ [0,1] measures match quality. This uses the **whole envelope pattern**, so it is
   robust to echoes and to takes that contain more than one clap.
2. *Per-frame timestamp offset* (`read_frames_offset`) — the difference of the first server-clock stamps in
   each `frames.json`: `ts_offset = (ref.first_ms − cam.first_ms) / 1000` seconds. Independent of audio.

**Clap position vs. clap offset — the key distinction.** `clap_time` returns the single loudest transient
(argmax of the envelope). It is used only to *position the trim window* (and as the dashed "naive" marker in
the report). The actual inter-camera *offset* is the cross-correlation lag, not the difference of argmaxes.
When a take has several claps/echoes the two disagree; the cross-correlation is the trustworthy one and the
report shows both so the disagreement is visible.

**Decision (per camera).** If audio confidence ≥ `MIN_CONFIDENCE = 0.15`, method = `audio_xcorr` and the
clap's position in that camera is `ref_clap + lag`. Else, if a timestamp offset exists, method = `timestamp`
(`ref_clap + ts_offset`). Otherwise `fallback_zero`.

**Common timeline + render.** Let `pre = min(2.0 s, min clap position)`. Each clip's aligned start is
`clap_position − pre`; the common duration is the minimum overlap after that start. ffmpeg re-encodes each clip
from its aligned start to a **constant frame rate** with the claps coincident (`-vf fps=F -r F`, H.264 + AAC),
written to `sync/aligned/<label>.mp4`. The target rate **F is the session's configured capture fps** (from
`session.json`), not a fixed value.

**Outputs.** `sync/offsets.json` records, per camera: `lag_vs_ref_s`, `audio_confidence`, `ts_offset_s`,
`method`, `aligned_start_s`, and `eff_fps_from_frames`. With `--verify`, the tool re-extracts audio from the
*aligned* clips and re-measures the lag vs. the reference, writing `residual_ms` — the post-alignment
inter-camera error, which the report compares against the frame period (1000/F ms).

**Caveat on `eff_fps_from_frames`.** `frames.json` is logged by `requestVideoFrameCallback`, which fires on
*display* refresh, not on encoded capture frames, so the reported effective fps is a display-rate proxy and can
differ markedly from the container fps. It does not affect the audio-based sync.

**How the report visualizes this.** The per-recording report shows: the audio envelope per camera on its own
file timeline (with the loudest-transient markers and a blue playback cursor), the normalized
cross-correlation curve (chosen lag vs. the naive loudest-peak lag), a synchronized side-by-side player of the
aligned clips, the residual verdict vs. the frame period, and the audio-vs-timestamp agreement check.

## Validation of sync (the critical test)
Record a **common high-rate visual event** seen by all cameras simultaneously — e.g., a monitor/phone
showing a millisecond timer, or a single LED flash. After running the sync tool, measure the residual
inter-camera offset of that event. Report the **distribution of sync error in ms** and compare to the
frame period (1/fps). Document achieved accuracy in the HTML report. Do not assume — measure.

## Prior art / sources (Q1 journals + credible engineering)
- Uhlrich et al., **OpenCap: Human movement dynamics from smartphone videos**, *PLOS Computational Biology* 2023
  (Q1). Multi-iPhone markerless biomechanics; videos time-synced in the cloud via cross-correlation of
  keypoint velocities, then triangulated. https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1011462
- **Pose2Sim** (perfanalytics) — markerless kinematics; synchronizes views via keypoint motion before
  triangulation. https://github.com/perfanalytics/pose2sim
- **caliscope** (mprib) — multicamera calibration from synchronized video for 3D triangulation; exports TRC/OpenSim.
  https://github.com/mprib/caliscope
- Ansari et al., **Wireless Software Synchronization of Multiple Distributed Cameras**, ICCP 2019
  (libsoftwaresync) — NTP-style clock sync + phase alignment across phones. https://arxiv.org/pdf/1812.09366
- Web APIs: MDN `getUserMedia`, `MediaRecorder`, `HTMLVideoElement.requestVideoFrameCallback`,
  Screen Wake Lock API (for keeping the capture page awake).
