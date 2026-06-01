# Error log

Append an entry whenever a step's validation gate fails. Format:

```
## YYYY-MM-DD — Step N: <title>
- Symptom: what failed and how it was observed (the validation that did not pass)
- Diagnosis: root cause
- Fix / updated procedure: what changed before re-running
- Re-run result: pass / fail
```

## 2026-05-31 — Step 8: sync.py validation run
- Symptom: first validation run crashed — `SyntaxError: f-string: unmatched '['` (nested same-quote
  f-string in the offsets print loop), under Python 3.10.
- Diagnosis: nested f-string reusing single quotes; not valid before 3.12.
- Fix: precompute `lag_str`/`conf_str` before the print; no nested f-strings.
- Also fixed pre-validation: timestamp-fallback offset had inverted sign
  (`clap_in_cam = ref_clap + (start_ref - start_cam)`, not `start_cam - start_ref`).
- Re-run result: PASS. Synthetic 2-cam data, true offset +300 ms -> recovered +300.0 ms;
  post-alignment residual 0.0 ms (1 ms envelope resolution) for both cameras; aligned clips rendered.

## 2026-05-31 — preview relay validation run
- Symptom: WS test client got `ServerDisconnectedError`.
- Diagnosis: not a code bug — `certs/` now exists, so app.py serves HTTPS; the test used `http://`.
- Fix: run automated tests with `--certs /tmp/nocerts` to force HTTP. PASS: preview_on / relay / preview_off.

## 2026-06-01 — Full validation re-run (clean environment)
- Symptom: host-side smoke FAILED on first run — `ConnectionRefusedError 127.0.0.1` (app never came up).
- Diagnosis: environment, not code. Two causes in a fresh sandbox: (1) runtime deps absent
  (`segno`, `aiohttp`, `scipy`) so `app.py` aborted with `ModuleNotFoundError: segno`;
  (2) `tests/run_all.sh` writes the app log to `/tmp/cts_app.log`, which was not writable in the
  sandbox (`Permission denied`), masking the real startup error.
- Fix / updated procedure: `pip install -r requirements.txt`; start app with a writable log path.
  (On the user's macOS host both are non-issues — `/tmp` is writable and deps install normally.)
- Re-run result: PASS. Host smoke 15/15, offline sync 2/2 (recovered +300.0 ms, residual 0.0 ms),
  calibration 5/5 (intrinsics exact, extrinsic reproj 0.0 px, translation 0.0 m).
- Still blocked (device-gated, need iPhone(s) on LAN): Step 1, Step 4, and real-footage Step 8/7b.

## 2026-06-01 — Step 1/4 validated on device; Step 8 on real footage
- Event: user recorded a real 2-iPhone session (`20260601-205418-ba9f04`) over HTTPS on campus Wi-Fi.
- Step 1 PASS: capture page served over HTTPS, getUserMedia permitted on both phones (trusted mkcert cert).
- Step 4 PASS: both clips saved; `frames.json` monotonic; clap present; effective fps reported
  (note: rVFC logged ~60 Hz display callbacks vs ~30 fps container — timestamps are display-rate, not encoded-frame).
- Step 8 real residual: synchronized trigger landed at ~8 ms; audio-clap x-corr offset cam-pyfz −38 ms
  (sharp dominant peak, conf 0.85); post-alignment residual −22 ms ≈ 0.66 frame at 30 fps (within one frame).
  Caveat: the take had multiple claps, so the naive loudest-peak detector disagreed (547 ms) while the
  cross-correlation correctly rejected it. Recommend a single isolated clap for unambiguous future takes.

## 2026-06-01 — Step 8: sync.py crash on report regeneration
- Symptom: `python sync.py <session> --verify` (re-run by the new per-recording report) crashed with
  `PermissionError: Operation not permitted: 'cam-nz6i.mp4'` at `shutil.rmtree(adir)`, leaving
  `residual_ms: None`, so the report showed 0 ms.
- Diagnosis: `shutil.rmtree` hard-fails when a stale `aligned/` clip can't be unlinked (restricted/locked
  filesystem). The crash happened before residual computation.
- Fix / updated procedure: `shutil.rmtree(adir, ignore_errors=True)` then recreate the dir; ffmpeg `-y`
  overwrites stale clips anyway.
- Re-run result: PASS. residual_ms = {cam-nz6i 0.0, cam-pyfz −22.0}; tests/test_sync.py still 2/2.
