#!/usr/bin/env python3
"""
Automated smoke test for app.py — validates the parts that do NOT need a real iPhone:
health, pairing/QR, WS registry+roster, clock-sync ping/pong, synchronized-trigger
broadcast, config push, session metadata, and chunked resumable upload (+hash).

Exits non-zero on any failed assertion. Device-only checks (camera capture, iOS HTTPS)
are out of scope here and must be run on hardware.
"""
import asyncio, hashlib, json, os, sys
import aiohttp

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8080")
results = []
def check(name, ok, extra=""):
    results.append((name, ok, extra))
    print(("PASS" if ok else "FAIL"), name, extra)

async def main():
    async with aiohttp.ClientSession() as s:
        # Step 0 — health
        async with s.get(f"{BASE}/health") as r:
            j = await r.json()
            check("health 200/ok", r.status == 200 and j.get("status") == "ok", str(r.status))

        # Step 1 — pairing + QR
        async with s.get(f"{BASE}/pairing") as r:
            p = await r.json()
            check("pairing has capture_url", "/capture?token=" in p.get("capture_url", ""), p.get("capture_url",""))
        async with s.get(f"{BASE}/qr", params={"data": p["capture_url"]}) as r:
            svg = await r.text()
            check("qr returns svg", r.status == 200 and "<svg" in svg)

        # Step 2/3/4/6 — camera WS: register, clock sync, config, start
        cam = await s.ws_connect(f"{BASE}/ws")
        await cam.send_json({"type":"register","role":"camera","label":"cam-A","info":{}})
        reg = await cam.receive_json()
        check("camera registered", reg.get("type")=="registered" and bool(reg.get("device_id")), reg.get("device_id",""))

        # clock ping/pong
        await cam.send_json({"type":"clock_ping","t0": 1000.0})
        pong = await cam.receive_json()
        check("clock_pong t1<=t2", pong.get("type")=="clock_pong" and pong["t1"]<=pong["t2"])
        await cam.send_json({"type":"clock_report","offset_ms":12.3,"rtt_ms":8.0,"jitter_ms":4.0})

        # control WS sees the camera in roster (with clock)
        ctl = await s.ws_connect(f"{BASE}/ws")
        await ctl.send_json({"type":"register","role":"control"})
        hello = await ctl.receive_json(); check("control hello", hello.get("type")=="hello")

        # roster (read FIRST, before preview traffic interleaves on the control socket)
        roster = None
        for _ in range(5):
            m = await ctl.receive_json()
            if m.get("type")=="roster": roster = m; break
        cams = (roster or {}).get("cameras", [])
        check("roster lists camera", any(c["label"]=="cam-A" for c in cams), f"{len(cams)} cams")
        check("roster has clock offset", any((c.get('clock') or {}).get('offset_ms')==12.3 for c in cams))

        # live preview: control presence -> camera told preview_on; frame relays back tagged
        on = False
        for _ in range(6):
            m = await cam.receive_json()
            if m.get("type")=="preview_on": on=True; break
        check("camera got preview_on", on)
        await cam.send_json({"type":"preview","data":"data:image/jpeg;base64,ZZ"})
        rel = None
        for _ in range(6):
            m = await ctl.receive_json()
            if m.get("type")=="preview": rel=m; break
        check("preview relayed to control", bool(rel) and rel.get("label")=="cam-A")

        # config push: control -> camera
        await ctl.send_json({"type":"config","config":{"width":1280,"height":720,"fps":30,"facing":"user"}})
        cfg = await cam.receive_json()
        check("camera got config", cfg.get("type")=="config" and cfg["config"]["width"]==1280)

        # synchronized start broadcast
        await ctl.send_json({"type":"start"})
        # camera should get a start with a future t0
        startmsg = None
        for _ in range(3):
            m = await cam.receive_json()
            if m.get("type")=="start": startmsg=m; break
        check("camera got start w/ future t0", bool(startmsg) and startmsg["t0_server_ms"]>0
              and startmsg["clap_at_server_ms"]>startmsg["t0_server_ms"])

        # Step 7 — session metadata
        async with s.post(f"{BASE}/session", json={"title":"t","subject":"S1","trial":"r1",
                          "notes":"n","tags":["a","b"],"config":{"fps":60}}) as r:
            sess = await r.json()
            sid = sess["session_id"]
            check("session created", bool(sid) and sess["subject"]=="S1", sid)

        # Step 5 — chunked resumable upload with hash verify
        payload = bytes(range(256)) * 8000  # ~2 MB
        sha = hashlib.sha256(payload).hexdigest()
        CH = 700_000; off = 0
        while off < len(payload):
            end = min(off+CH, len(payload)); final = end>=len(payload)
            headers = {"X-Session":sid,"X-Device":"cam-A","X-Filename":"video.mp4",
                       "X-Offset":str(off),"X-Final":"1" if final else "0"}
            if final: headers["X-SHA256"]=sha
            async with s.post(f"{BASE}/upload", data=payload[off:end], headers=headers) as r:
                jj = await r.json()
            off = end
        check("upload verified hash", jj.get("verified") is True, jj.get("sha256","")[:12])
        check("upload size matches", jj.get("size")==len(payload), str(jj.get('size')))

        # Reports — /sessions lists the just-created session
        async with s.get(f"{BASE}/sessions") as r:
            sl = await r.json()
            ids = [x["session_id"] for x in sl.get("sessions", [])]
            check("sessions lists new session", sid in ids, f"{len(ids)} sessions")
        # /report rejects path traversal and unknown sessions
        async with s.get(f"{BASE}/report", params={"session":"../../etc"}) as r:
            check("report blocks traversal", r.status == 404, str(r.status))
        async with s.get(f"{BASE}/report", params={"session":"__nope__"}) as r:
            check("report 404 unknown session", r.status == 404, str(r.status))
        # /sync route guards (no heavy pipeline invoked for these)
        async with s.post(f"{BASE}/sync", params={"session":"../../etc"}) as r:
            check("sync blocks traversal", r.status == 404, str(r.status))
        async with s.post(f"{BASE}/sync", params={"session":"__nope__"}) as r:
            check("sync 404 unknown session", r.status == 404, str(r.status))

        await cam.close(); await ctl.close()

    ok = all(r[1] for r in results)
    print(f"\n{sum(1 for r in results if r[1])}/{len(results)} checks passed")
    sys.exit(0 if ok else 1)

asyncio.run(main())
