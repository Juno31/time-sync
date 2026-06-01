#!/usr/bin/env python3
"""
session_report.py — per-recording report (sync accuracy + clap detection diagnostic).

For one recorded session it produces a self-contained dark-theme HTML page with:
  - a header verdict (inter-camera sync error vs the frame period),
  - the clap-detection diagnostic as INLINE SVG: each camera's audio envelope on its
    own file timeline (loudest transient marked) + the normalized cross-correlation
    curve used to pick the offset (so a multi-clap / ambiguous take is visible),
  - a Step-4 capture table per camera (frames, monotonic timestamps, effective fps),
  - a sync table (offset, method, confidence, residual) with the independent
    audio-vs-timestamp cross-check.

Heavy work reuses sync.py (numpy / scipy / ffmpeg). No matplotlib: plots are hand-built
SVG, matching report.py, so the live host needs no extra runtime deps to *serve* it.

Usage:
  python session_report.py sessions/<id> [--fps 30] [--force]
Writes: sessions/<id>/report.html  (and refreshes sessions/<id>/sync/offsets.json)
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
from scipy.signal import correlate

# Reuse the exact sync primitives so the report matches what sync.py computed.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync import (extract_envelope, clap_time, ENV_RATE, read_frames_offset,
                  ffprobe_meta, discover_cameras)

ROOT = Path(__file__).resolve().parent
PALETTE = ["#6aa9ff", "#ef6b6b", "#5ad17a", "#e2b340", "#c084fc", "#22d3ee"]


def configured_fps(session: Path, default: float = 30.0) -> float:
    """Target CFR for sync/report = the session's requested capture fps (from session.json),
    so the user's configuration is honored instead of a hardcoded 30."""
    try:
        cfg = json.loads((session / "session.json").read_text()).get("config", {})
        f = float(cfg.get("fps") or 0)
        return f if f > 0 else default
    except Exception:
        return default


# ----------------------------------------------------------------------------
# data gathering
# ----------------------------------------------------------------------------
def ensure_offsets(session: Path, fps: float, force: bool) -> dict:
    """Run sync.py --verify if offsets.json is missing/stale, then load it."""
    off = session / "sync" / "offsets.json"
    if force or not off.exists():
        subprocess.run([sys.executable, str(ROOT / "sync.py"), str(session),
                        "--fps", str(fps), "--verify"],
                       capture_output=True, text=True)
    if off.exists():
        return json.loads(off.read_text())
    return {}


def frames_monotonic(cam_dir: Path):
    """(n_frames, is_monotonic, eff_fps) from frames.json."""
    f = cam_dir / "frames.json"
    if not f.exists():
        return 0, None, None
    try:
        frames = json.loads(f.read_text())
        ts = [fr["server_ms"] for fr in frames]
        mono = all(b >= a for a, b in zip(ts, ts[1:])) if len(ts) > 1 else None
        _, eff = read_frames_offset(cam_dir)
        return len(ts), mono, eff
    except Exception:
        return 0, None, None


def gather(session: Path, fps: float, force: bool):
    sync = ensure_offsets(session, fps, force)
    cams = discover_cameras(session)
    ref = sync.get("reference") or (cams[0][0] if cams else None)

    work = Path(tempfile.mkdtemp(prefix="report_"))
    rows = []
    env = {}
    clap = {}
    for label, d, vid in cams:
        dur, src_fps = ffprobe_meta(vid)
        e, ok = extract_envelope(vid, work)
        env[label] = e if ok else None
        clap[label] = clap_time(e) if ok else None
        n, mono, eff = frames_monotonic(d)
        co = (sync.get("cameras", {}) or {}).get(label, {})
        rows.append({
            "label": label, "is_ref": label == ref,
            "dur": dur, "src_fps": src_fps, "eff_fps": eff,
            "n_frames": n, "monotonic": mono, "audio_ok": ok,
            "lag_ms": (co.get("lag_vs_ref_s") or 0.0) * 1000 if co.get("lag_vs_ref_s") is not None else None,
            "ts_off_ms": (co.get("ts_offset_s") * 1000) if co.get("ts_offset_s") is not None else None,
            "conf": co.get("audio_confidence"),
            "method": co.get("method", "—"),
            "residual_ms": (sync.get("residual_ms") or {}).get(label),
        })
    return sync, ref, rows, env, clap, work


# ----------------------------------------------------------------------------
# inline-SVG plotting (no matplotlib)
# ----------------------------------------------------------------------------
def _downsample_max(y: np.ndarray, n: int):
    """Max-pool to n points so sharp transients (claps) survive downsampling."""
    if len(y) <= n:
        return np.arange(len(y)), y
    idx = np.linspace(0, len(y), n + 1).astype(int)
    xs, ys = [], []
    for a, b in zip(idx[:-1], idx[1:]):
        b = max(b, a + 1)
        seg = y[a:b]
        k = int(np.argmax(np.abs(seg)))
        xs.append(a + k)
        ys.append(seg[k])
    return np.array(xs), np.array(ys)


def _poly(xs, ys, x0, x1, y0, y1, xmin, xmax, ymin, ymax, color, w=1.0):
    """Map data -> svg coords, return a <polyline>."""
    if xmax == xmin or ymax == ymin:
        return ""
    px = x0 + (np.asarray(xs) - xmin) / (xmax - xmin) * (x1 - x0)
    py = y1 - (np.asarray(ys) - ymin) / (ymax - ymin) * (y1 - y0)
    pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(px, py))
    return f"<polyline fill='none' stroke='{color}' stroke-width='{w}' points='{pts}'/>"


def svg_envelopes(env, clap, ref):
    """Panel 1: each camera's envelope over its own file timeline, clap marked."""
    labels = [l for l in env if env[l] is not None]
    if not labels:
        return "<p class='muted'>no usable audio in this session.</p>"
    W, H, L, R, T, B = 760, 230, 48, 16, 24, 34
    maxdur = max(len(env[l]) for l in labels) / ENV_RATE
    ymax = max(float(np.max(env[l])) for l in labels)
    body = []
    # axes
    body.append(f"<line x1='{L}' y1='{H-B}' x2='{W-R}' y2='{H-B}' stroke='#2a313b'/>")
    body.append(f"<line x1='{L}' y1='{T}' x2='{L}' y2='{H-B}' stroke='#2a313b'/>")
    for k in range(0, int(maxdur) + 1, max(1, int(maxdur) // 8 or 1)):
        x = L + k / maxdur * (W - L - R)
        body.append(f"<line x1='{x:.0f}' y1='{H-B}' x2='{x:.0f}' y2='{H-B+4}' stroke='#2a313b'/>"
                    f"<text x='{x:.0f}' y='{H-B+16}' fill='#8b95a1' font-size='10' text-anchor='middle'>{k}s</text>")
    legend = []
    for i, l in enumerate(labels):
        color = PALETTE[i % len(PALETTE)]
        n = len(env[l])
        xs, ys = _downsample_max(env[l], 720)
        body.append(_poly(xs, ys, L, W - R, T, H - B, 0, n, 0, ymax, color, 0.8))
        if clap[l] is not None:
            cx = L + (clap[l] * ENV_RATE) / n * (W - L - R)
            body.append(f"<line x1='{cx:.1f}' y1='{T}' x2='{cx:.1f}' y2='{H-B}' "
                        f"stroke='{color}' stroke-dasharray='4 3' stroke-width='1.1'/>")
        tag = " (ref)" if l == ref else ""
        clab = f"{clap[l]:.3f}s" if clap[l] is not None else "n/a"
        legend.append(f"<tspan fill='{color}'>&#9632;</tspan> <tspan fill='#cfd6de'>{l}{tag} · loudest @ {clab}</tspan>")
    leg = "<text x='{}' y='{}' font-size='11'>".format(L, T - 8) + \
          "   ".join(legend) + "</text>"
    # playback cursor (moved by the synchronized player via #envCursor); geometry in data-*
    cursor = (f"<line id='envCursor' data-x0='{L}' data-x1='{W-R}' data-tmax='{maxdur:.4f}' "
              f"x1='{L}' y1='{T}' x2='{L}' y2='{H-B}' stroke='#9fd0ff' stroke-width='1.5' opacity='0'/>")
    return (f"<div class='chart'><div class='ctitle'>Audio envelope per camera "
            f"(own file timeline) — dashed line = single loudest transient; "
            f"<span style='color:#9fd0ff'>blue cursor</span> = video playback position</div>"
            f"<svg viewBox='0 0 {W} {H}' width='100%'>{leg}{''.join(body)}{cursor}</svg></div>")


def svg_xcorr(env, clap, ref):
    """Panel 2: normalized cross-correlation of each non-ref camera vs the reference."""
    if env.get(ref) is None:
        return ""
    others = [l for l in env if l != ref and env[l] is not None]
    if not others:
        return ("<p class='muted'>Single camera with audio — no inter-camera "
                "cross-correlation (need ≥ 2 cameras for a sync measurement).</p>")
    W, H, L, R, T, B = 760, 230, 48, 16, 24, 34
    ref_env = env[ref]
    span = 1500  # ms window
    body = [f"<line x1='{L}' y1='{H-B}' x2='{W-R}' y2='{H-B}' stroke='#2a313b'/>",
            f"<line x1='{L}' y1='{T}' x2='{L}' y2='{H-B}' stroke='#2a313b'/>"]
    # zero lag line
    xz = L + (0 + span) / (2 * span) * (W - L - R)
    body.append(f"<line x1='{xz:.0f}' y1='{T}' x2='{xz:.0f}' y2='{H-B}' stroke='#3a424d' stroke-dasharray='3 3'/>")
    for k in (-1000, -500, 0, 500, 1000):
        x = L + (k + span) / (2 * span) * (W - L - R)
        body.append(f"<text x='{x:.0f}' y='{H-B+16}' fill='#8b95a1' font-size='10' text-anchor='middle'>{k}</text>")
    legend = []
    for i, l in enumerate(others):
        color = PALETTE[(i + 1) % len(PALETTE)]
        c = correlate(env[l], ref_env, mode="full", method="fft")
        denom = np.sqrt(np.sum(env[l] ** 2) * np.sum(ref_env ** 2)) or 1.0
        cn = c / denom
        lags = (np.arange(len(c)) - (len(ref_env) - 1)) / ENV_RATE * 1000
        m = (lags > -span) & (lags < span)
        lags_w, cn_w = lags[m], cn[m]
        xs, ys = _downsample_max(cn_w, 720)
        # map xs (indices into cn_w) to lag ms
        lag_xs = lags_w[np.clip(xs, 0, len(lags_w) - 1)]
        body.append(_poly(lag_xs, ys, L, W - R, T, H - B, -span, span, 0, 1.0, color, 1.0))
        pk = int(np.argmax(cn_w))
        pk_lag, pk_val = lags_w[pk], cn_w[pk]
        px = L + (pk_lag + span) / (2 * span) * (W - L - R)
        body.append(f"<line x1='{px:.1f}' y1='{T}' x2='{px:.1f}' y2='{H-B}' stroke='{color}' stroke-width='1.4'/>")
        # loudest-peak-diff lag (the naive estimate) for comparison
        if clap[l] is not None and clap[ref] is not None:
            amd = (clap[l] - clap[ref]) * 1000
            if -span < amd < span:
                ax = L + (amd + span) / (2 * span) * (W - L - R)
                body.append(f"<line x1='{ax:.1f}' y1='{T}' x2='{ax:.1f}' y2='{H-B}' "
                            f"stroke='{color}' stroke-dasharray='4 3' stroke-width='1' opacity='0.7'/>")
        legend.append(f"<tspan fill='{color}'>&#9632;</tspan> "
                      f"<tspan fill='#cfd6de'>{l}: peak {pk_lag:+.0f} ms (corr {pk_val:.2f})</tspan>")
    leg = "<text x='{}' y='{}' font-size='11'>".format(L, T - 8) + "   ".join(legend) + "</text>"
    return (f"<div class='chart'><div class='ctitle'>Normalized cross-correlation vs "
            f"reference ({ref}) — solid = chosen lag, dashed = naive loudest-peak diff. "
            f"x-axis lag (ms)</div>"
            f"<svg viewBox='0 0 {W} {H}' width='100%'>{leg}{''.join(body)}</svg></div>")


# ----------------------------------------------------------------------------
# verdict + tables
# ----------------------------------------------------------------------------
def verdict(rows, fps):
    frame_ms = 1000.0 / fps
    res = [abs(r["residual_ms"]) for r in rows if r.get("residual_ms") is not None and not r["is_ref"]]
    if not res:
        return ("n/a", "#8b95a1",
                "Need ≥ 2 cameras with a shared clap to measure inter-camera sync error.")
    worst = max(res)
    if worst <= frame_ms / 2:
        return (f"{worst:.0f} ms", "#5ad17a",
                f"Sub-frame: worst residual {worst:.0f} ms ≤ ½ frame ({frame_ms/2:.0f} ms). Cameras land on the same frame.")
    if worst <= frame_ms:
        return (f"{worst:.0f} ms", "#e2b340",
                f"Within one frame: worst residual {worst:.0f} ms < {frame_ms:.0f} ms frame period. Adequate for {fps:.0f} fps reconstruction.")
    return (f"{worst:.0f} ms", "#ef6b6b",
            f"Off by > one frame ({frame_ms:.0f} ms): re-record with a single sharp clap, phones equidistant.")


def crosscheck_note(rows, fps):
    frame_ms = 1000.0 / fps
    msgs = []
    for r in rows:
        if r["is_ref"] or r["lag_ms"] is None or r["ts_off_ms"] is None:
            continue
        diff = abs(r["lag_ms"] - (-r["ts_off_ms"]))  # both express cam-vs-ref; signs differ by convention
        # use raw magnitude agreement on |audio lag| vs |timestamp offset|
        d = abs(abs(r["lag_ms"]) - abs(r["ts_off_ms"]))
        ok = d <= frame_ms
        msgs.append((r["label"], r["lag_ms"], r["ts_off_ms"], d, ok))
    return msgs


def video_panel(session: Path) -> str:
    """Synchronized side-by-side player of the aligned clips (served via /media/).
    Playing them together is the visual proof of sync; the scrubber locks all videos."""
    adir = session / "sync" / "aligned"
    clips = sorted(adir.glob("*.mp4")) if adir.exists() else []
    if not clips:
        return ("<h2>Synchronized playback</h2>"
                "<p class='muted'>No aligned clips yet — run <b>Sync</b> for this session. "
                "The time-aligned, common-timeline videos are written to <code>sync/aligned/</code> "
                "and shown here.</p>")
    sid = session.name
    tiles = []
    for i, c in enumerate(clips):
        url = "/media/" + quote(sid) + "/sync/aligned/" + quote(c.name)
        tiles.append(
            f"<div class='vtile'><video class='sv' data-i='{i}' muted playsinline "
            f"preload='metadata' src='{url}'></video><div class='vcap'>{c.stem}</div></div>")
    grid = "<div class='vgrid'>" + "".join(tiles) + "</div>"
    css = ("<style>"
           ".vgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;}"
           ".vtile{background:#000;border:1px solid #222;border-radius:10px;overflow:hidden;position:relative;}"
           ".sv{width:100%;max-height:380px;display:block;background:#000;object-fit:contain;}"
           ".vcap{position:absolute;left:8px;bottom:8px;font-size:12px;background:rgba(0,0,0,.6);"
           "padding:2px 8px;border-radius:999px;}"
           ".vbar{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap;}"
           ".vbtn{background:#1b2129;color:#e7eaee;border:1px solid #2a313b;border-radius:8px;"
           "padding:7px 12px;font-size:13px;cursor:pointer;}"
           "#vseek{flex:1;min-width:160px;}"
           "</style>")
    controls = ("<div class='vbar'>"
                "<button id='vplay' class='vbtn'>&#9654; Play</button>"
                "<button id='vrestart' class='vbtn'>&#8635; Restart</button>"
                "<input id='vseek' type='range' min='0' max='100' value='0' step='0.01'>"
                "<span id='vtime' class='muted'>0.00 / 0.00 s</span></div>")
    script = """<script>
    (function(){
      var vids=[].slice.call(document.querySelectorAll('video.sv'));
      if(!vids.length) return;
      var master=vids[0], playing=false;
      var btn=document.getElementById('vplay'), seek=document.getElementById('vseek'),
          tl=document.getElementById('vtime'), rb=document.getElementById('vrestart');
      function fmt(t){return (t||0).toFixed(2);}
      // move the cursor on the clap-diagnostic waveform to the current playback time
      function updateCursor(t){
        var cur=document.getElementById('envCursor'); if(!cur||!window.__playmap) return;
        var x0=+cur.dataset.x0, x1=+cur.dataset.x1, tmax=+cur.dataset.tmax, rs=window.__playmap.refStart||0;
        var ft=rs+(t||0), fx=(tmax>0)?Math.max(0,Math.min(tmax,ft))/tmax:0;
        var x=x0+fx*(x1-x0);
        cur.setAttribute('x1',x); cur.setAttribute('x2',x); cur.setAttribute('opacity','0.9');
      }
      master.addEventListener('loadedmetadata',function(){
        seek.max=master.duration||0; tl.textContent='0.00 / '+fmt(master.duration)+' s'; updateCursor(0); });
      function playAll(){ vids.forEach(function(v){v.play();}); playing=true; btn.innerHTML='&#10074;&#10074; Pause'; }
      function pauseAll(){ vids.forEach(function(v){v.pause();}); playing=false; btn.innerHTML='&#9654; Play'; }
      btn.onclick=function(){ playing?pauseAll():playAll(); };
      rb.onclick=function(){ vids.forEach(function(v){v.currentTime=0;}); seek.value=0; updateCursor(0); };
      master.addEventListener('timeupdate',function(){
        seek.value=master.currentTime;
        tl.textContent=fmt(master.currentTime)+' / '+fmt(master.duration)+' s';
        vids.forEach(function(v){ if(v!==master && Math.abs(v.currentTime-master.currentTime)>0.08) v.currentTime=master.currentTime; });
        updateCursor(master.currentTime);
      });
      master.addEventListener('ended',pauseAll);
      seek.addEventListener('input',function(){ var t=parseFloat(seek.value)||0;
        vids.forEach(function(v){ v.currentTime=t; }); updateCursor(t); });
    })();
    </script>"""
    return ("<h2>Synchronized playback <span class='muted' style='text-transform:none;font-weight:400;'>"
            "(aligned clips, common timeline)</span></h2>" + css + controls + grid +
            "<p class='muted'>Press Play — motion should line up across cameras. Drag the scrubber to "
            "inspect a single moment. Audio is muted (all cameras heard the same clap).</p>" + script)


def build_html(session: Path, sync, ref, rows, env, clap, fps):
    sj = session / "session.json"
    meta = json.loads(sj.read_text()) if sj.exists() else {}
    frame_ms = 1000.0 / fps
    vtext, vcolor, vexpl = verdict(rows, fps)

    # capture (Step 4) table
    cap_rows = []
    for r in rows:
        mono = "—" if r["monotonic"] is None else ("yes" if r["monotonic"] else "NO")
        mono_c = "#5ad17a" if r["monotonic"] else ("#ef6b6b" if r["monotonic"] is False else "#8b95a1")
        cap_rows.append(
            f"<tr><td class='stitle'>{r['label']}{' (ref)' if r['is_ref'] else ''}</td>"
            f"<td>{r['dur']:.2f} s</td><td>{r['n_frames']}</td>"
            f"<td style='color:{mono_c}'>{mono}</td>"
            f"<td>{('%.1f'%r['eff_fps']) if r['eff_fps'] else '—'}</td>"
            f"<td>{('%.1f'%r['src_fps']) if r['src_fps'] else '—'}</td>"
            f"<td>{('%.2f'%r['conf']) if r['conf'] is not None else '—'}</td></tr>")

    # sync table
    sync_rows = []
    for r in rows:
        lag = "ref" if r["is_ref"] else (f"{r['lag_ms']:+.1f} ms" if r["lag_ms"] is not None else "—")
        res = "0.0 ms (ref)" if r["is_ref"] else (f"{r['residual_ms']:+.1f} ms" if r["residual_ms"] is not None else "—")
        sync_rows.append(
            f"<tr><td class='stitle'>{r['label']}</td><td>{r['method']}</td>"
            f"<td>{lag}</td>"
            f"<td>{('%+.1f ms'%r['ts_off_ms']) if r['ts_off_ms'] is not None else '—'}</td>"
            f"<td><b>{res}</b></td></tr>")

    # cross-check
    cc = crosscheck_note(rows, fps)
    cc_html = ""
    for label, lag, ts, d, ok in cc:
        col = "#5ad17a" if ok else "#e2b340"
        verdict_cc = "agree" if ok else "disagree"
        cc_html += (f"<li><b>{label}</b>: audio clap {lag:+.0f} ms vs timestamp {ts:+.0f} ms "
                    f"&rarr; |Δ| = {d:.0f} ms (<span style='color:{col}'>{verdict_cc}</span> "
                    f"vs {frame_ms:.0f} ms frame).</li>")
    if not cc_html:
        cc_html = "<li class='muted'>Cross-check needs ≥ 2 cameras with both audio and frame timestamps.</li>"

    panels = svg_envelopes(env, clap, ref) + svg_xcorr(env, clap, ref)
    playback = video_panel(session)
    # reference clip's start offset (file-time of aligned t=0) — drives the waveform cursor
    ref_start = float(((sync.get("cameras") or {}).get(ref) or {}).get("aligned_start_s") or 0.0)
    playmap_js = '<script>window.__playmap={"refStart":%s};</script>' % ref_start

    title = meta.get("title") or session.name
    created = time.strftime('%Y-%m-%d %H:%M', time.localtime(
        (meta.get("created_ms") or time.time() * 1000) / 1000))

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — sync report</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family:-apple-system,system-ui,sans-serif; background:#0b0d10; color:#e7eaee; line-height:1.5; }}
  .wrap {{ max-width:880px; margin:0 auto; padding:30px 22px 80px; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.07em; color:#8b95a1; margin:30px 0 10px; }}
  .sub {{ color:#8b95a1; font-size:13px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th,td {{ text-align:left; padding:9px 11px; border-bottom:1px solid #1c2128; font-size:13px; vertical-align:top; }}
  th {{ color:#8b95a1; font-weight:600; font-size:12px; }}
  .stitle {{ font-weight:600; }}
  .muted {{ color:#8b95a1; font-size:12px; }}
  .hero {{ background:#11151a; border:1px solid #222; border-radius:14px; padding:20px 22px; margin:16px 0; }}
  .hero .n {{ font-size:38px; font-weight:800; line-height:1; }}
  .hero .l {{ color:#8b95a1; font-size:13px; margin-bottom:8px; }}
  .chart {{ background:#11151a; border:1px solid #222; border-radius:12px; padding:14px 16px; margin:12px 0; }}
  .ctitle {{ font-size:12px; color:#cfd6de; margin-bottom:8px; }}
  ul {{ margin:8px 0; padding-left:20px; }} li {{ font-size:13px; margin:4px 0; }}
  code {{ background:#1b2129; padding:1px 6px; border-radius:6px; font-size:12px; }}
</style></head><body><div class="wrap">

<h1>{title}</h1>
<div class="sub">session <code>{session.name}</code>
{(' · subject ' + meta.get('subject')) if meta.get('subject') else ''}
{(' · trial ' + meta.get('trial')) if meta.get('trial') else ''}
 · recorded {created} · target {fps:.0f} fps (frame = {frame_ms:.1f} ms)</div>

<div class="hero">
  <div class="l">Inter-camera sync error (worst residual after alignment)</div>
  <div class="n" style="color:{vcolor}">{vtext}</div>
  <div class="sub" style="margin-top:8px">{vexpl}</div>
</div>

{playback}
{playmap_js}

<h2>Clap detection diagnostic</h2>
{panels}
<p class="muted">A clean take has one sharp transient and a single dominant cross-correlation peak.
Multiple peaks mean multiple claps/echoes; the cross-correlation (solid line) uses the whole pattern
and is more reliable than the naive loudest-peak pick (dashed).</p>

<h2>Capture check (Step 4)</h2>
<table><thead><tr><th>Camera</th><th>Duration</th><th>Frames</th><th>Monotonic</th>
<th>Eff. fps</th><th>Container fps</th><th>Clap conf.</th></tr></thead>
<tbody>{''.join(cap_rows)}</tbody></table>

<h2>Synchronization (Step 8)</h2>
<table><thead><tr><th>Camera</th><th>Method</th><th>Audio lag vs ref</th>
<th>Timestamp offset</th><th>Residual</th></tr></thead>
<tbody>{''.join(sync_rows)}</tbody></table>

<h2>Independent cross-check (audio vs timestamps)</h2>
<ul>{cc_html}</ul>
<p class="muted">Two independent estimates per camera: the audio clap and the per-frame server
timestamps. Agreement within one frame corroborates the offset; large disagreement flags an
ambiguous clap or clock jitter.</p>

</div></body></html>"""


def generate(session, fps=None, force=False) -> Path:
    session = Path(session).resolve()
    if fps is None:
        fps = configured_fps(session)   # honor the session's requested fps
    sync, ref, rows, env, clap, work = gather(session, fps, force)
    html = build_html(session, sync, ref, rows, env, clap, fps)
    out = session / "report.html"
    out.write_text(html)
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--fps", type=float, default=None,
                    help="target CFR; default = the session's configured capture fps")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = generate(args.session, args.fps, args.force)
    print("Wrote", out)


if __name__ == "__main__":
    main()
