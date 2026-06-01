#!/usr/bin/env python3
"""
Offline validation of sync.py on synthetic 2-camera footage with a KNOWN inter-camera
offset. Asserts that sync.py recovers the offset and drives the post-alignment residual
to ~0. Self-contained: builds the data in a tempdir, requires ffmpeg + numpy + scipy.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parent.parent
TRUE_OFFSET_MS = 300.0      # cam1 clap occurs this much later in its own file
SR = 16000
DUR = 8.0

def build(base: Path):
    def make(label, clap_t, first_ms):
        d = base / label; d.mkdir(parents=True, exist_ok=True)
        a = np.random.randn(int(SR*DUR)) * 0.01
        t = np.arange(int(SR*0.05)) / SR
        burst = np.sin(2*np.pi*1000*t) * 0.9 * np.hanning(len(t))
        s = int(clap_t*SR); a[s:s+len(burst)] += burst
        wav = d / "a.wav"
        wavfile.write(wav, SR, np.int16(np.clip(a, -1, 1)*32767))
        subprocess.run(["ffmpeg","-y","-f","lavfi","-i",f"color=c=blue:s=320x240:d={DUR}:r=30",
                        "-i",str(wav),"-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",
                        "-shortest", str(d/"video.mp4")], capture_output=True)
        wav.unlink()
        fr = [{"server_ms": first_ms + i*1000/30.0} for i in range(int(DUR*30))]
        (d/"frames.json").write_text(json.dumps(fr))
    make("cam0", 5.0, 1_000_000.0)
    make("cam1", 5.0 + TRUE_OFFSET_MS/1000.0, 1_000_000.0 - TRUE_OFFSET_MS)

def main():
    tmp = Path(tempfile.mkdtemp(prefix="synctest_"))
    sess = tmp / "synthtest"; sess.mkdir()
    build(sess)
    r = subprocess.run([sys.executable, str(ROOT/"sync.py"), str(sess),
                        "--fps","30","--verify"], capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print("sync.py failed:", r.stderr); sys.exit(1)
    out = json.loads((sess/"sync"/"offsets.json").read_text())
    lag = out["cameras"]["cam1"]["lag_vs_ref_s"]*1000
    resid = max(abs(v) for v in out.get("residual_ms", {0:99}).values())
    ok_lag = abs(lag - TRUE_OFFSET_MS) <= 20     # within 20 ms
    ok_res = resid <= 16                          # within half a frame @30fps
    print(("PASS" if ok_lag else "FAIL"), f"recovered lag {lag:.1f} ms (true {TRUE_OFFSET_MS})")
    print(("PASS" if ok_res else "FAIL"), f"residual after alignment {resid:.1f} ms")
    sys.exit(0 if (ok_lag and ok_res) else 1)

if __name__ == "__main__":
    main()
