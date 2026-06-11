#!/usr/bin/env python3
"""
camera time sync — minimal local host process.

One process (aiohttp) does the three irreducible jobs:
  1. serve the HTTPS pages (control UI + phone capture page),
  2. run a WebSocket hub for control + clock-sync + synchronized trigger,
  3. accept streamed uploads and write recordings to disk.

No framework, no database, no build step. Filesystem only.

Run:
    python app.py [--host 0.0.0.0] [--port 8443] [--certs certs]
If certs/cert.pem and certs/key.pem exist -> HTTPS (required by iOS for getUserMedia).
Otherwise -> HTTP (fine for localhost dev / automated tests; iOS camera will NOT work over HTTP).
"""
import argparse
import asyncio
import hashlib
import json
import socket
import ssl
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from aiohttp import web, WSMsgType
import segno

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
SESSIONS = ROOT / "sessions"
SESSIONS.mkdir(exist_ok=True)

# Reported in /health, /host and the WS hello so the control UI can detect a stale
# host process serving old code (a recurring source of confusion — see CLAUDE.md backlog).
APP_VERSION = "2026.06.11b"

# Synchronized-trigger lead times (ms), in server-clock terms.
START_LEAD_MS = 3000     # default; the control UI can override per recording (lead_ms)
MIN_LEAD_MS, MAX_LEAD_MS = 1000, 30000
COUNTDOWN_MS = 5000      # clap countdown window after start


def now_ms() -> float:
    """Server wall-clock in milliseconds (the shared time base)."""
    return time.time() * 1000.0


def lan_ip() -> str:
    """Best-effort primary LAN IP of this host."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _cert_covers_ip(cert_pem: Path, ip: str) -> bool:
    """True if the cert's SAN already includes this IP (so it works on the LAN)."""
    import shutil as _sh
    if not _sh.which("openssl"):
        return True  # can't inspect -> don't churn the cert
    try:
        import subprocess
        r = subprocess.run(["openssl", "x509", "-in", str(cert_pem), "-noout", "-text"],
                           capture_output=True, text=True)
        return ip in r.stdout
    except Exception:
        return True


def ensure_cert(certs: Path, lan: str):
    """Keep TLS working without the terminal: if the cert is missing or doesn't
    cover the current LAN IP (e.g. the network changed), regenerate it with mkcert
    automatically. No-op if mkcert isn't installed (host then serves HTTP)."""
    import shutil as _sh
    import subprocess
    cert_pem, key_pem = certs / "cert.pem", certs / "key.pem"
    have = cert_pem.exists() and key_pem.exists()
    if have and _cert_covers_ip(cert_pem, lan):
        return
    if not _sh.which("mkcert"):
        if not have:
            print("[camera-time-sync] No TLS cert and mkcert not installed. "
                  "Install mkcert once for HTTPS (iOS camera requires it).")
        return
    certs.mkdir(parents=True, exist_ok=True)
    print(f"[camera-time-sync] {'regenerating' if have else 'creating'} TLS cert for {lan} (mkcert)…")
    subprocess.run(["mkcert", "-cert-file", str(cert_pem), "-key-file", str(key_pem),
                    "localhost", "127.0.0.1", lan], capture_output=True, text=True)


# ----------------------------------------------------------------------------
# In-memory state (no DB). Devices and control clients live only while connected.
# ----------------------------------------------------------------------------
class Hub:
    def __init__(self):
        self.cameras = {}   # device_id -> dict(ws, label, info, clock, status)
        self.controls = set()  # set of ws
        self.session = None    # current session dict (mirrors session.json)
        self.autosync = True   # auto-run sync + report when all uploads land
        self.participants = set()  # device_ids that recorded the current session
        self.processed = set()     # session_ids already auto-processed (fire once)
        # Capture config auto-applied to every camera on connect. Default = the only
        # iOS combination that yields 60 fps (60 fps format exists only at <=720p;
        # 1080p caps to 30 — WebKit bug 179994). Overwritten by any pushed "config".
        self.last_config = {"width": 1280, "height": 720, "fps": 60, "facing": "environment"}

    # ---- roster broadcast -------------------------------------------------
    def roster(self):
        return [
            {
                "device_id": d,
                "label": c["label"],
                "status": c["status"],
                "clock": c["clock"],
                "info": c["info"],
                "settings": c.get("settings"),
            }
            for d, c in self.cameras.items()
        ]

    async def push_roster(self):
        msg = {"type": "roster", "cameras": self.roster(),
               "session": self.session}
        await self.broadcast_controls(msg)

    async def broadcast_controls(self, msg):
        dead = []
        for ws in self.controls:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.controls.discard(ws)

    async def broadcast_cameras(self, msg):
        dead = []
        for d, c in list(self.cameras.items()):
            try:
                await c["ws"].send_json(msg)
            except Exception:
                dead.append(d)
        for d in dead:
            self.cameras.pop(d, None)

    async def set_preview(self, on: bool):
        """Tell cameras to start/stop sending low-rate preview frames (saves uplink when no one is watching)."""
        await self.broadcast_cameras({"type": "preview_on" if on else "preview_off"})

    # ---- automatic sync + report on upload completion ---------------------
    async def maybe_autosync(self):
        """Fire once per session when every camera that recorded it has uploaded."""
        if not self.autosync:
            return
        sid = (self.session or {}).get("session_id")
        if not sid or sid in self.processed or not self.participants:
            return
        if not all(self.cameras.get(d, {}).get("status") == "uploaded"
                   for d in self.participants):
            return
        self.processed.add(sid)
        asyncio.create_task(self.autoprocess(sid))

    async def autoprocess(self, sid: str):
        """Run sync + build the per-recording report off the event loop, notify controls."""
        await self.broadcast_controls({"type": "auto_status", "session_id": sid, "state": "running"})
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _run_session_pipeline, sid)
            await self.broadcast_controls({"type": "auto_status", "session_id": sid,
                                           "state": "done", **result})
        except Exception as e:
            await self.broadcast_controls({"type": "auto_status", "session_id": sid,
                                           "state": "error", "error": str(e)})


def _session_fps(d: Path, default: float = 30.0) -> float:
    """Target CFR = the session's configured capture fps (session.json), not a fixed 30."""
    try:
        cfg = json.loads((d / "session.json").read_text()).get("config", {})
        f = float(cfg.get("fps") or 0)
        return f if f > 0 else default
    except Exception:
        return default


def _run_session_pipeline(sid: str, fps: float = None) -> dict:
    """Blocking: run sync (--verify) and build sessions/<sid>/report.html, return a summary.
    Called in an executor so the event loop stays responsive."""
    import session_report  # lazy: needs numpy/scipy/ffmpeg on the host
    d = SESSIONS / sid
    if fps is None:
        fps = _session_fps(d)
    session_report.generate(str(d), fps, force=True)
    off = d / "sync" / "offsets.json"
    data = json.loads(off.read_text()) if off.exists() else {}
    frame_ms = 1000.0 / fps
    resid = data.get("residual_ms") or {}
    worst = max((abs(v) for k, v in resid.items() if k != data.get("reference")), default=None)
    verdict = ("n/a" if worst is None else
               "sub-frame" if worst <= frame_ms / 2 else
               "within one frame" if worst <= frame_ms else "off by > one frame")
    return {"worst_residual_ms": worst, "verdict": verdict, "frame_ms": round(frame_ms, 1)}


hub = Hub()


# ----------------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------------
async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "camera-time-sync",
        "version": APP_VERSION,
        "server_ms": now_ms(),
        "cameras": len(hub.cameras),
        "controls": len(hub.controls),
    })


async def index(request):
    return web.FileResponse(WEB / "control" / "index.html")


async def capture(request):
    return web.FileResponse(WEB / "capture" / "index.html")


async def qr(request):
    """Return an SVG QR for arbitrary text (used to pair phones to the capture URL)."""
    data = request.query.get("data", "")
    if not data:
        return web.Response(status=400, text="missing data")
    buf = segno.make(data, error="m")
    import io
    out = io.BytesIO()
    # omitsize + an explicit viewBox make the SVG scale to whatever box the UI puts it
    # in. segno's default fixed width/height (and no viewBox) CROPS the drawing when the
    # container is smaller than the native size — an unscannable, clipped QR.
    buf.save(out, kind="svg", scale=6, border=2, omitsize=True)
    w, h = buf.symbol_size(scale=6, border=2)
    svg = out.getvalue().replace(b"<svg ", f'<svg viewBox="0 0 {w} {h}" '.encode(), 1)
    return web.Response(body=svg, content_type="image/svg+xml")


async def pairing_info(request):
    """Capture URL + session token the control UI renders as a QR."""
    scheme = "https" if request.app["tls"] else "http"
    host = request.app["lan_ip"]
    port = request.app["port"]
    token = request.app["token"]
    url = f"{scheme}://{host}:{port}/capture?token={token}"
    return web.json_response({"capture_url": url, "token": token,
                              "scheme": scheme, "host": host, "port": port})


def _slug(s: str) -> str:
    """Filesystem-safe token from a metadata field (keep alnum / - / _; drop the rest)."""
    s = (s or "").strip().replace(" ", "-")
    return "".join(ch for ch in s if ch.isalnum() or ch in "-_")


def _session_id_from_meta(body: dict) -> str:
    """Build the session-folder name from the input values: title_subject_trial.
    Falls back to a timestamp if all are empty, and de-duplicates on collision so a
    repeated trial never overwrites an earlier take."""
    parts = [_slug(body.get(k, "")) for k in ("title", "subject", "trial")]
    base = "_".join(p for p in parts if p)
    if not base:
        base = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    sid = base
    i = 2
    while (SESSIONS / sid).exists():
        sid = f"{base}-{i}"
        i += 1
    return sid


async def create_session(request):
    """Persist session metadata (title, subject, trial, notes, tags, config)."""
    body = await request.json()
    sid = body.get("session_id") or _session_id_from_meta(body)
    sid = Path(sid).name  # never allow path separators in the folder name
    sdir = SESSIONS / sid
    sdir.mkdir(parents=True, exist_ok=True)
    session = {
        "session_id": sid,
        "created_ms": now_ms(),
        "title": body.get("title", ""),
        "subject": body.get("subject", ""),
        "trial": body.get("trial", ""),
        "notes": body.get("notes", ""),
        "tags": body.get("tags", []),
        "config": body.get("config", {}),
        "cameras": [c["label"] for c in hub.cameras.values()],
    }
    (sdir / "session.json").write_text(json.dumps(session, indent=2))
    hub.session = session
    await hub.push_roster()
    return web.json_response(session)


async def get_session(request):
    sid = request.match_info["sid"]
    f = SESSIONS / sid / "session.json"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(json.loads(f.read_text()))


def _safe_session_dir(sid: str):
    """Resolve a session id to its dir, rejecting path traversal."""
    sid = Path(sid or "").name  # strip any path components
    d = (SESSIONS / sid).resolve()
    if not str(d).startswith(str(SESSIONS.resolve())) or not d.is_dir():
        return None
    return d


NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


async def host_status(request):
    """Setup status for the control UI (so the user manages from the browser, not a terminal)."""
    import shutil as _sh
    return web.json_response({
        "lan_ip": request.app["lan_ip"],
        "port": request.app["port"],
        "scheme": "https" if request.app["tls"] else "http",
        "https": request.app["tls"],
        "mkcert_available": bool(_sh.which("mkcert")),
        "version": APP_VERSION,
    }, headers=NO_CACHE)


async def list_sessions(request):
    """List recorded sessions (newest first) for the control UI's reports panel."""
    out = []
    for d in sorted((p for p in SESSIONS.iterdir() if p.is_dir()),
                    key=lambda p: p.name, reverse=True):
        cams = [c.name for c in d.iterdir()
                if c.is_dir() and c.name != "sync" and list(c.glob("video.*"))]
        meta = {}
        sj = d / "session.json"
        if sj.exists():
            try:
                meta = json.loads(sj.read_text())
            except Exception:
                meta = {}
        if not cams and not sj.exists():
            continue  # skip empties
        # sync residual summary (if this session has been synced)
        off = d / "sync" / "offsets.json"
        worst, verdict, frame_ms = None, None, None
        if off.exists():
            try:
                data = json.loads(off.read_text())
                frame_ms = round(1000.0 / _session_fps(d), 1)
                r = data.get("residual_ms") or {}
                ref = data.get("reference")
                worst = max((abs(v) for k, v in r.items() if k != ref), default=None)
                if worst is not None:
                    verdict = ("sub-frame" if worst <= frame_ms / 2 else
                               "within one frame" if worst <= frame_ms else "off by > one frame")
            except Exception:
                pass
        out.append({
            "session_id": d.name,
            "title": meta.get("title", ""),
            "subject": meta.get("subject", ""),
            "trial": meta.get("trial", ""),
            "created_ms": meta.get("created_ms"),
            "cameras": cams,
            "n_cameras": len(cams),
            "has_report": (d / "report.html").exists(),
            "has_sync": off.exists(),
            "residual_ms": worst,
            "verdict": verdict,
            "frame_ms": frame_ms,
        })
    return web.json_response({"sessions": out}, headers=NO_CACHE)


async def run_sync(request):
    """Run post-hoc time synchronization on one session's recorded videos.
    POST /sync?session=<id> [fps=30]. Runs sync.py --verify, then returns a summary
    (per-camera offset/method/confidence + residual vs frame period) as JSON."""
    sid = request.query.get("session", "")
    d = _safe_session_dir(sid)
    if d is None:
        return web.json_response({"ok": False, "error": "session not found"},
                                 status=404, headers=NO_CACHE)
    q = request.query.get("fps")
    fps = float(q) if q else _session_fps(d)
    cams = [c for c in d.iterdir()
            if c.is_dir() and c.name != "sync" and list(c.glob("video.*"))]
    if len(cams) < 1:
        return web.json_response({"ok": False, "error": "no recorded video in this session"},
                                 status=400, headers=NO_CACHE)
    import sys as _sys
    proc = await asyncio.create_subprocess_exec(
        _sys.executable, str(ROOT / "sync.py"), str(d), "--fps", str(fps), "--verify",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out_b, _ = await proc.communicate()
    off_f = d / "sync" / "offsets.json"
    if proc.returncode != 0 or not off_f.exists():
        return web.json_response(
            {"ok": False, "error": "sync failed (ffmpeg/numpy/scipy required on host)",
             "log": (out_b or b"").decode("utf-8", "replace")[-1500:]},
            status=500, headers=NO_CACHE)
    data = json.loads(off_f.read_text())
    frame_ms = 1000.0 / fps
    resid = data.get("residual_ms") or {}
    worst = max((abs(v) for k, v in resid.items() if k != data.get("reference")), default=None)
    verdict = ("n/a" if worst is None else
               "sub-frame" if worst <= frame_ms / 2 else
               "within one frame" if worst <= frame_ms else "off by > one frame")
    return web.json_response({
        "ok": True, "session": d.name, "reference": data.get("reference"),
        "target_fps": fps, "frame_ms": round(frame_ms, 1),
        "residual_ms": resid, "worst_residual_ms": worst, "verdict": verdict,
        "cameras": {k: {"lag_ms": (v.get("lag_vs_ref_s") or 0) * 1000,
                        "method": v.get("method"),
                        "confidence": v.get("audio_confidence")}
                    for k, v in (data.get("cameras") or {}).items()},
    }, headers=NO_CACHE)


async def report(request):
    """Generate (if needed) and serve the per-session sync/clap report.
    Query: session=<id> [force=1] [fps=30]. The live host needs numpy/scipy/ffmpeg
    for generation; if absent we return 503 with the install hint (serving is dep-free)."""
    sid = request.query.get("session", "")
    d = _safe_session_dir(sid)
    if d is None:
        return web.Response(status=404, text="session not found")
    force = request.query.get("force", "0") == "1"
    q = request.query.get("fps")
    fps = float(q) if q else _session_fps(d)
    out = d / "report.html"
    if force or not out.exists():
        try:
            import session_report  # lazy: keeps the host bootable without analysis deps
        except Exception as e:
            return web.Response(status=503, content_type="text/html",
                text=f"<body style='font-family:system-ui;background:#0b0d10;color:#e7eaee;padding:40px'>"
                     f"<h2>Report engine unavailable</h2><p>Install the analysis deps on the host:</p>"
                     f"<pre style='background:#11151a;padding:14px;border-radius:8px'>"
                     f"pip install -r requirements.txt</pre><p class=muted>({e})</p></body>")
        try:
            loop = asyncio.get_event_loop()
            out = await loop.run_in_executor(
                None, lambda: session_report.generate(str(d), fps, force))
        except Exception as e:
            return web.Response(status=500, content_type="text/html",
                text=f"<body style='font-family:system-ui;background:#0b0d10;color:#e7eaee;padding:40px'>"
                     f"<h2>Report generation failed</h2><pre>{e}</pre>"
                     f"<p class=muted>Often: a recording is still uploading, or ffmpeg is missing.</p></body>")
    return web.FileResponse(out, headers=NO_CACHE)


async def upload(request):
    """
    Chunked, resumable upload. Each chunk carries headers:
      X-Session, X-Device, X-Filename, X-Offset (byte offset), X-Final (1 on last),
      X-SHA256 (full-file hash, only on final chunk).
    Chunks are appended at the given offset. Returns current size; on final, verifies hash.
    """
    sid = request.headers.get("X-Session")
    device = request.headers.get("X-Device")
    fname = request.headers.get("X-Filename")
    offset = int(request.headers.get("X-Offset", "0"))
    final = request.headers.get("X-Final", "0") == "1"
    expected_sha = request.headers.get("X-SHA256", "")
    if not (sid and device and fname):
        return web.json_response({"error": "missing headers"}, status=400)
    # sanitize
    sid = Path(sid).name  # no path traversal via X-Session
    device = "".join(ch for ch in device if ch.isalnum() or ch in "-_")
    fname = Path(fname).name
    ddir = SESSIONS / sid / device
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / fname

    data = await request.read()
    mode = "r+b" if path.exists() else "wb"
    with open(path, mode) as f:
        f.seek(offset)
        f.write(data)
    size = path.stat().st_size

    result = {"size": size, "offset": offset, "received": len(data)}
    if final and expected_sha:
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        result["sha256"] = h
        result["verified"] = (h == expected_sha)
    # let control UIs show live upload progress + data rate (Motive-style control deck)
    try:
        total = int(request.headers.get("X-Total", "0"))
    except ValueError:
        total = 0
    await hub.broadcast_controls({
        "type": "upload_progress", "session_id": sid, "device": device,
        "filename": fname, "size": size, "total": total, "received": len(data),
        "final": final, "verified": result.get("verified")})
    return web.json_response(result)


# ----------------------------------------------------------------------------
# WebSocket hub: cameras + control clients
# ----------------------------------------------------------------------------
async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    role = None
    device_id = None
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                m = json.loads(msg.data)
            except Exception:
                continue
            t = m.get("type")

            # ---- registration ------------------------------------------------
            if t == "register":
                role = m.get("role")
                if role == "control":
                    first = len(hub.controls) == 0
                    hub.controls.add(ws)
                    await ws.send_json({"type": "hello", "server_ms": now_ms(),
                                        "start_lead_ms": START_LEAD_MS,
                                        "version": APP_VERSION})
                    await hub.push_roster()
                    if first:
                        await hub.set_preview(True)  # someone is watching -> cameras start previewing
                else:  # camera
                    device_id = m.get("device_id") or uuid.uuid4().hex[:8]
                    hub.cameras[device_id] = {
                        "ws": ws,
                        "label": m.get("label") or device_id,
                        "info": m.get("info", {}),
                        "clock": {"offset_ms": None, "rtt_ms": None, "jitter_ms": None},
                        "status": "connected",
                        "settings": None,   # negotiated {width,height,fps,...} reported by the phone
                    }
                    await ws.send_json({"type": "registered", "device_id": device_id,
                                        "server_ms": now_ms()})
                    # Auto-apply the current capture config so every phone configures
                    # itself on connect (no manual "Push config" needed).
                    await ws.send_json({"type": "config", "config": hub.last_config})
                    # If a control client is already watching, tell this camera to
                    # start previewing immediately (the set_preview broadcast only
                    # fires when the *first* control joins, which is usually before
                    # any phones have connected).
                    if hub.controls:
                        await ws.send_json({"type": "preview_on"})
                    await hub.push_roster()

            # ---- clock sync (NTP-like) --------------------------------------
            elif t == "clock_ping":
                t1 = now_ms()
                await ws.send_json({"type": "clock_pong", "t0": m.get("t0"),
                                    "t1": t1, "t2": now_ms()})
            elif t == "clock_report" and device_id in hub.cameras:
                hub.cameras[device_id]["clock"] = {
                    "offset_ms": m.get("offset_ms"),
                    "rtt_ms": m.get("rtt_ms"),
                    "jitter_ms": m.get("jitter_ms"),
                }
                await hub.push_roster()

            # ---- live preview frames (camera -> controls) -------------------
            elif t == "preview" and device_id in hub.cameras:
                await hub.broadcast_controls({
                    "type": "preview", "device_id": device_id,
                    "label": hub.cameras[device_id]["label"], "data": m.get("data")})

            # ---- negotiated camera settings (actual W/H/fps) ----------------
            elif t == "cam_settings" and device_id in hub.cameras:
                hub.cameras[device_id]["settings"] = m.get("settings")
                await hub.push_roster()

            # ---- camera status updates --------------------------------------
            elif t == "status" and device_id in hub.cameras:
                st = m.get("status", "connected")
                hub.cameras[device_id]["status"] = st
                if st == "recording":
                    hub.participants.add(device_id)   # took part in the current session
                await hub.push_roster()
                if st == "uploaded":
                    await hub.maybe_autosync()        # fire when all participants are done

            # ---- control commands -------------------------------------------
            elif t == "config":   # push capture config to all cameras + remember it for new joiners
                hub.last_config = m.get("config", {}) or hub.last_config
                await hub.broadcast_cameras({"type": "config", "config": hub.last_config})
            elif t == "clock_resync":   # control asks all phones to re-estimate clock offset now
                await hub.broadcast_cameras({"type": "clock_resync"})
            elif t == "set_autosync":
                hub.autosync = bool(m.get("on", True))
            elif t == "start":
                hub.participants = set()  # reset for the new recording
                try:
                    lead = float(m.get("lead_ms") or START_LEAD_MS)
                except (TypeError, ValueError):
                    lead = START_LEAD_MS
                lead = max(MIN_LEAD_MS, min(MAX_LEAD_MS, lead))
                t0 = now_ms() + lead
                await hub.broadcast_cameras({
                    "type": "start",
                    "session_id": (hub.session or {}).get("session_id"),
                    "t0_server_ms": t0,
                    "clap_at_server_ms": t0 + COUNTDOWN_MS,
                })
                await hub.broadcast_controls({"type": "started", "t0_server_ms": t0,
                                              "clap_at_server_ms": t0 + COUNTDOWN_MS})
            elif t == "stop":
                await hub.broadcast_cameras({"type": "stop", "server_ms": now_ms()})
                await hub.broadcast_controls({"type": "stopped", "server_ms": now_ms()})

    finally:
        if role == "control":
            hub.controls.discard(ws)
            if not hub.controls:
                await hub.set_preview(False)  # nobody watching -> cameras stop previewing
        elif device_id:
            hub.cameras.pop(device_id, None)
            await hub.push_roster()
    return ws


async def delete_session(request):
    """Delete a recorded session and all its files. POST /session_delete?session=<id>."""
    sid = request.query.get("session", "")
    d = _safe_session_dir(sid)
    if d is None:
        return web.json_response({"ok": False, "error": "session not found"},
                                 status=404, headers=NO_CACHE)
    import shutil as _sh
    try:
        _sh.rmtree(d)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500, headers=NO_CACHE)
    # forget any in-memory state tied to this session
    if hub.session and hub.session.get("session_id") == d.name:
        hub.session = None
    hub.processed.discard(d.name)
    return web.json_response({"ok": True, "session_id": d.name}, headers=NO_CACHE)


def build_app(tls: bool, lan: str, port: int, token: str) -> web.Application:
    app = web.Application(client_max_size=512 * 1024 * 1024)  # 512 MB chunk ceiling
    app["tls"] = tls
    app["lan_ip"] = lan
    app["port"] = port
    app["token"] = token
    app.router.add_get("/health", health)
    app.router.add_get("/", index)
    app.router.add_get("/capture", capture)
    app.router.add_get("/qr", qr)
    app.router.add_get("/pairing", pairing_info)
    app.router.add_post("/session", create_session)
    app.router.add_get("/session/{sid}", get_session)
    app.router.add_get("/sessions", list_sessions)
    app.router.add_get("/host", host_status)
    app.router.add_get("/report", report)
    app.router.add_post("/sync", run_sync)
    app.router.add_post("/session_delete", delete_session)
    app.router.add_post("/upload", upload)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", WEB)  # serve shared assets if any
    # serve recorded + aligned clips to the report's synchronized player
    # (aiohttp static handles HTTP Range, so video seeking works)
    app.router.add_static("/media/", SESSIONS, show_index=False)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--certs", default="certs")
    ap.add_argument("--no-https", action="store_true",
                    help="serve plain HTTP and skip cert auto-creation (tests / local dev; "
                         "iOS camera will NOT work)")
    ap.add_argument("--open", action="store_true", help="open the control UI in a browser on startup")
    args = ap.parse_args()

    certs = ROOT / args.certs
    lan = lan_ip()
    if not args.no_https:
        ensure_cert(certs, lan)  # auto-create/refresh the cert for this network (no terminal)
    cert_pem, key_pem = certs / "cert.pem", certs / "key.pem"
    ssl_ctx = None
    if not args.no_https and cert_pem.exists() and key_pem.exists():
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(str(cert_pem), str(key_pem))

    token = uuid.uuid4().hex[:8]
    app = build_app(tls=ssl_ctx is not None, lan=lan, port=args.port, token=token)

    scheme = "https" if ssl_ctx else "http"
    print(f"[camera-time-sync] {scheme}://{lan}:{args.port}/  (control UI)")
    print(f"[camera-time-sync] capture: {scheme}://{lan}:{args.port}/capture?token={token}")
    if not ssl_ctx:
        print("[camera-time-sync] WARNING: no certs found -> HTTP. iOS camera needs HTTPS; "
              "run scripts/setup_certs.sh and install the cert on each iPhone.")
    if args.open:
        url = f"{scheme}://127.0.0.1:{args.port}/"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()
