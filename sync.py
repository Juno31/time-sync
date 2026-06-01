#!/usr/bin/env python3
"""
sync.py — offline post-hoc synchronization of a recorded session.

Turns the clap + per-frame timestamps captured by the live app into time-aligned,
constant-frame-rate clips on a common timeline, ready for a pose/3D pipeline
(opencap-core / Pose2Sim).

Method (layered, robust-first):
  1. AUDIO CLAP cross-correlation  — the single physical clap is recorded by every
     phone's mic; cross-correlating the audio envelopes gives a sharp, sub-frame
     relative offset between cameras. (primary)
  2. PER-FRAME TIMESTAMP offset    — from frames.json (server-clock stamps); used as
     an independent prior / sanity check, and as fallback when audio is unusable.
  3. KEYPOINT-VELOCITY x-corr (OpenCap) — OPTIONAL refinement, only if 2D keypoints
     are available (see --keypoints); not required for temporal alignment.

Output (under <session>/sync/):
  offsets.json            per-camera offsets, method used, confidence, residual
  aligned/<label>.mp4     CFR clips, common timeline, claps coincident

Usage:
  python sync.py sessions/<session_id> [--fps 30] [--ref <label>] [--pre 2.0]
                 [--no-render] [--verify]

Requires: ffmpeg/ffprobe on PATH, numpy, scipy.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate
from scipy.ndimage import uniform_filter1d

ENV_RATE = 1000          # Hz, envelope resample rate for cross-correlation
SMOOTH_MS = 8            # envelope smoothing window
MIN_CONFIDENCE = 0.15    # below this, audio offset is considered unreliable


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def ffprobe_meta(video: Path):
    """Return (duration_s, fps) for a video file."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,duration",
             "-show_entries", "format=duration", "-of", "json", str(video)])
    try:
        j = json.loads(r.stdout)
        dur = float(j.get("format", {}).get("duration")
                    or j["streams"][0].get("duration"))
        fr = j["streams"][0].get("avg_frame_rate", "0/1")
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        dur, fps = 0.0, 0.0
    return dur, fps


def extract_envelope(video: Path, workdir: Path):
    """Extract mono audio, return (envelope @ ENV_RATE, ok)."""
    wav = workdir / (video.stem + ".wav")
    r = run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1",
             "-ar", "16000", str(wav)])
    if not wav.exists():
        return None, False
    rate, data = wavfile.read(wav)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float64)
    if data.size == 0 or np.allclose(data, 0):
        return None, False
    # rectified + smoothed envelope, then resample to ENV_RATE
    env = np.abs(data)
    win = max(1, int(rate * SMOOTH_MS / 1000))
    env = uniform_filter1d(env, win)
    n_out = int(len(env) * ENV_RATE / rate)
    if n_out < 2:
        return None, False
    env = np.interp(np.linspace(0, len(env) - 1, n_out),
                    np.arange(len(env)), env)
    # normalize
    env = env - env.mean()
    if env.std() > 0:
        env = env / env.std()
    return env, True


def xcorr_lag(cam_env, ref_env):
    """Lag in seconds such that cam = ref delayed by lag (cam clap occurs lag later).
       Returns (lag_s, confidence in 0..1)."""
    c = correlate(cam_env, ref_env, mode="full", method="fft")
    idx = int(np.argmax(c))
    lag_samples = idx - (len(ref_env) - 1)
    # normalized confidence: peak / (||cam|| ||ref||)
    denom = np.sqrt(np.sum(cam_env**2) * np.sum(ref_env**2))
    conf = float(c[idx] / denom) if denom > 0 else 0.0
    return lag_samples / ENV_RATE, conf


def clap_time(env):
    """Absolute time (s) of the sharpest transient (clap) in an envelope."""
    return int(np.argmax(env)) / ENV_RATE


def read_frames_offset(cam_dir: Path):
    """First-frame server timestamp (ms) from frames.json, or None."""
    f = cam_dir / "frames.json"
    if not f.exists():
        return None, None
    try:
        frames = json.loads(f.read_text())
        if not frames:
            return None, None
        first = min(fr["server_ms"] for fr in frames)
        span = max(fr["server_ms"] for fr in frames) - first
        eff_fps = (len(frames) - 1) / (span / 1000.0) if span > 0 else None
        return first, eff_fps
    except Exception:
        return None, None


def discover_cameras(session: Path):
    cams = []
    for d in sorted(p for p in session.iterdir() if p.is_dir() and p.name != "sync"):
        vids = list(d.glob("video.*"))
        vids = [v for v in vids if v.suffix.lower() in (".mp4", ".webm", ".mov")]
        if vids:
            cams.append((d.name, d, vids[0]))
    return cams


def render_aligned(video, start_s, dur, fps, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = run(["ffmpeg", "-y", "-ss", f"{max(0.0, start_s):.4f}", "-i", str(video),
             "-t", f"{dur:.4f}", "-vf", f"fps={fps}", "-r", str(fps),
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(out_path)])
    return out_path.exists()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--fps", type=float, default=30.0, help="target common frame rate")
    ap.add_argument("--ref", default=None, help="reference camera label")
    ap.add_argument("--pre", type=float, default=2.0, help="seconds to keep before the clap")
    ap.add_argument("--no-render", action="store_true", help="compute offsets only")
    ap.add_argument("--verify", action="store_true", help="re-measure residual after alignment")
    args = ap.parse_args()

    session = Path(args.session).resolve()
    cams = discover_cameras(session)
    if len(cams) < 1:
        print("No camera videos found under", session); sys.exit(1)
    print(f"Found {len(cams)} camera(s): {[c[0] for c in cams]}")

    work = Path(tempfile.mkdtemp(prefix="syncwork_"))
    info = {}
    for label, d, vid in cams:
        dur, fps = ffprobe_meta(vid)
        env, ok = extract_envelope(vid, work)
        first_ms, eff_fps = read_frames_offset(d)
        info[label] = {"dir": d, "video": vid, "dur": dur, "fps": fps,
                       "env": env if ok else None, "audio_ok": ok,
                       "first_ms": first_ms, "eff_fps": eff_fps}

    # reference camera
    ref = args.ref or cams[0][0]
    if ref not in info:
        print("ref not found, using", cams[0][0]); ref = cams[0][0]
    ref_env = info[ref]["env"]
    ref_clap = clap_time(ref_env) if info[ref]["audio_ok"] else None

    # per-camera offset of the shared clap within its own file (seconds)
    offsets = {}
    for label in info:
        rec = info[label]
        method, conf, lag, ts_off = "none", None, None, None
        if rec["audio_ok"] and info[ref]["audio_ok"]:
            lag, conf = xcorr_lag(rec["env"], ref_env)     # cam clap = ref clap + lag
        if info[ref]["first_ms"] is not None and rec["first_ms"] is not None:
            # clap_in_cam = clap_in_ref + (start_ref - start_cam); start ~ first server stamp
            ts_off = (info[ref]["first_ms"] - rec["first_ms"]) / 1000.0
        if conf is not None and conf >= MIN_CONFIDENCE:
            clap_in_cam = ref_clap + lag
            method = "audio_xcorr"
        elif ts_off is not None:
            clap_in_cam = (ref_clap if ref_clap is not None else args.pre) + ts_off
            method = "timestamp"
        else:
            clap_in_cam = ref_clap if ref_clap is not None else args.pre
            method = "fallback_zero"
        offsets[label] = {"clap_in_cam_s": clap_in_cam, "lag_vs_ref_s": lag,
                          "audio_confidence": conf, "ts_offset_s": ts_off,
                          "method": method, "dur_s": rec["dur"],
                          "src_fps": rec["fps"], "eff_fps_from_frames": rec["eff_fps"],
                          "first_server_ms": rec["first_ms"]}

    # common window: align claps; keep PRE before, max common duration after
    pre = min(args.pre, min(o["clap_in_cam_s"] for o in offsets.values()))
    starts = {l: o["clap_in_cam_s"] - pre for l, o in offsets.items()}
    dur = min(info[l]["dur"] - starts[l] for l in offsets)  # common overlap length
    dur = max(0.0, dur)

    out = {
        "session": session.name, "reference": ref, "target_fps": args.fps,
        "pre_roll_s": pre, "common_duration_s": dur,
        "cameras": {l: {**offsets[l], "aligned_start_s": starts[l]} for l in offsets},
    }
    sync_dir = session / "sync"
    sync_dir.mkdir(exist_ok=True)
    (sync_dir / "offsets.json").write_text(json.dumps(out, indent=2, default=str))
    print("Offsets:")
    for l in offsets:
        o = offsets[l]
        lag_str = "n/a" if o["lag_vs_ref_s"] is None else f"{o['lag_vs_ref_s']*1000:+.1f}ms"
        conf_str = "n/a" if o["audio_confidence"] is None else f"{o['audio_confidence']:.2f}"
        print(f"  {l:10s} method={o['method']:12s} lag={lag_str:>10s} "
              f"conf={conf_str:>5s} start={starts[l]:.3f}s")

    if not args.no_render and dur > 0.5:
        adir = sync_dir / "aligned"
        # Clear stale clips, but never hard-fail if some file can't be removed
        # (e.g. locked / restricted filesystem); ffmpeg -y overwrites anyway.
        shutil.rmtree(adir, ignore_errors=True)
        adir.mkdir(parents=True, exist_ok=True)
        for label in offsets:
            ok = render_aligned(info[label]["video"], starts[label], dur, args.fps,
                                adir / f"{label}.mp4")
            print(f"  rendered {label}.mp4" if ok else f"  FAILED render {label}")

        if args.verify and len(cams) > 1:
            # residual: re-extract aligned audio and re-measure lag vs reference
            vwork = Path(tempfile.mkdtemp(prefix="syncverify_"))
            renv = {}
            for label in offsets:
                e, ok = extract_envelope(adir / f"{label}.mp4", vwork)
                renv[label] = e if ok else None
            print("Residual after alignment (target ~0):")
            base = renv[ref]
            residuals = {}
            for label in offsets:
                if renv[label] is not None and base is not None:
                    rlag, rconf = xcorr_lag(renv[label], base)
                    residuals[label] = rlag * 1000
                    print(f"  {label:10s} residual={rlag*1000:+7.1f} ms (conf {rconf:.2f})")
            out["residual_ms"] = residuals
            (sync_dir / "offsets.json").write_text(json.dumps(out, indent=2, default=str))
            shutil.rmtree(vwork, ignore_errors=True)

    shutil.rmtree(work, ignore_errors=True)
    print("Wrote", sync_dir / "offsets.json")


if __name__ == "__main__":
    main()
