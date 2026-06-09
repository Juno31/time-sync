# camera time sync — Windows host guide

Run the host on **Windows** instead of macOS. Everything the phones do is identical (they are still
ordinary iPhones in Safari); only the host setup, the HTTPS-certificate trust flow, and the launcher
differ. For the project overview, features, and how the synchronization works, see the main
[README.md](README.md) — this file only covers the Windows-specific parts.

> The host process (`app.py`, aiohttp) is cross-platform and runs fine on Windows. The original launcher
> and cert flow were written for macOS; this guide and `run.ps1` provide the Windows equivalents.

---

## Requirements

| Tool | Why | Install on Windows |
|---|---|---|
| **Python 3.10+** | host process + analysis | <https://www.python.org/downloads/> — tick **"Add python.exe to PATH"** in the installer |
| **ffmpeg / ffprobe** | audio extraction + VFR→CFR resampling | `winget install Gyan.FFmpeg` (or download from <https://ffmpeg.org/download.html> and add `bin` to PATH) |
| **mkcert** | locally-trusted HTTPS cert (iOS requires HTTPS for the camera) | `winget install FiloSottile.mkcert` (or `choco install mkcert`) |
| iPhone(s) | the cameras | iOS Safari, on the **same Wi-Fi** as the host |

Verify the tools are on PATH (open a **new** PowerShell window after installing):

```powershell
python --version
ffmpeg -version
mkcert -version
```

---

## Install

```powershell
git clone https://github.com/Juno31/time-sync.git
cd time-sync
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` creates `.venv`, installs the Python dependencies, and reports whether `ffmpeg` and `mkcert`
are present. It is safe to re-run.

---

## One-time HTTPS certificate (required — iOS blocks the camera over HTTP)

### 1. Trust the local CA on the Windows host

```powershell
mkcert -install
```

The host then **auto-creates and refreshes** the TLS cert for your current LAN IP every time it starts
(via `mkcert`), so you never regenerate it by hand when your network changes.

### 2. Trust the same CA on each iPhone (once per phone)

macOS uses AirDrop here; on Windows you transfer the root certificate another way. First find it:

```powershell
mkcert -CAROOT
```

That prints a folder containing **`rootCA.pem`**. Get that file onto the iPhone by any of:

- **Email** it to yourself and open the attachment on the phone, **or**
- put it in **OneDrive / iCloud Drive / Google Drive** and open it in the Files app, **or**
- connect the iPhone by **USB** and copy it across.

Then on the iPhone (same steps as macOS):

1. Open the `rootCA.pem` file → iOS says *Profile Downloaded*.
2. **Settings → General → VPN & Device Management** → tap the **mkcert** profile → **Install**.
3. **Settings → General → About → Certificate Trust Settings** → enable **full trust** for the mkcert root.

Without full trust, Safari will block the camera and the capture page shows "Camera blocked".

---

## iPhone capture settings — required for 60 fps

On **each** iPhone, turn **off Auto FPS** before recording, or iOS silently drops the camera to 30 fps in
lower light and your 60 fps target is not met:

**Settings → Camera → Record Video → Auto FPS → Off**

(On some iOS versions this is shown as picking a fixed rate such as "60 fps" rather than an "Auto" option.)
Record in adequate lighting — even with Auto FPS off, very low light reduces the effective frame rate.

---

## Run

```powershell
.\run.ps1                 # starts the host and opens the control UI
.\run.ps1 --port 9000     # any app.py flags pass through
```

`run.ps1` bootstraps the venv on first use, self-heals missing dependencies, stops any stale instance
holding the port, and launches the host. The control UI opens at `https://<LAN-IP>:8443/`; its header
shows host status (🔒 HTTPS + LAN IP).

If PowerShell blocks the script (execution policy), run it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File run.ps1
```

Manual fallback (equivalent to the launcher):

```powershell
.\.venv\Scripts\python app.py --open
```

### Let the phones reach the host (Windows Firewall)

The first time the host binds the port, Windows may prompt to allow Python through the firewall —
**allow it on Private networks**. If the phones can load the QR page but not connect, confirm an inbound
rule exists:

```powershell
New-NetFirewallRule -DisplayName "camera time sync" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443
```

(Run that line in an **Administrator** PowerShell. Match the port to whatever `--port` you use.)

### Record a trial

Identical to macOS: scan the QR in Safari on each phone → **Allow camera & mic** → set capture config and
session **Title / Subject / Trial** → **Start (synchronized)** → clap once at the **CLAP** prompt → **Stop**.
Clips upload to `sessions\<title_subject_trial>\<camera>\` and the report builds automatically. See the main
[README.md](README.md#record-a-trial) for the full walkthrough and the report description.

---

## Tests (no hardware needed)

```powershell
.\.venv\Scripts\python tests\smoke.py        # host smoke checks (run with the host stopped)
.\.venv\Scripts\python tests\test_sync.py    # offline sync validation
.\.venv\Scripts\python tests\test_calib.py   # calibration geometry validation
```

(`tests\run_all.sh` is a bash script and is macOS/Linux-oriented; on Windows run the Python test files
directly as above, or use **Git Bash / WSL** if you prefer the combined runner.)

---

## Windows-specific troubleshooting

- **`python` / `ffmpeg` / `mkcert` "not recognized"** — they are not on PATH. Re-open PowerShell after
  installing, or re-run the installer and ensure "Add to PATH" is selected.
- **`run.ps1` won't execute ("running scripts is disabled")** — use
  `powershell -ExecutionPolicy Bypass -File run.ps1`, or set the policy for your user:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- **Phones load the QR page but can't connect** — Windows Firewall is blocking inbound traffic; add the
  rule above and ensure the network profile is **Private**, not Public.
- **"Camera blocked" on the phone** — the mkcert root isn't fully trusted on that phone (redo the
  Certificate Trust Settings step), or the page was opened over `http`.
- **No 🔒 / serving HTTP** — `mkcert` isn't on PATH so the host couldn't make a cert. Install mkcert, run
  `mkcert -install`, and restart `run.ps1`.
- **Campus / eduroam / guest Wi-Fi** — these often block device-to-device traffic (client isolation). Use a
  home router, or turn on one iPhone's **Personal Hotspot** and join the PC + other phone to it.

For everything else (how sync works, the report, project structure, acknowledgements), see the main
[README.md](README.md).
