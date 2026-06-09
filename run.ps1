# Single launch command for the live host app on Windows (PowerShell).
#   .\run.ps1                  # start the host + open the control UI
#   .\run.ps1 --port 9000      # any app.py flags are passed through
# Windows equivalent of run.sh.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$PyVenv = ".\.venv\Scripts\python.exe"

# venv (created on first run)
if (-not (Test-Path $PyVenv)) {
  Write-Host "[run] creating .venv and installing deps..."
  python -m venv .venv
  & $PyVenv -m pip install --quiet --upgrade pip
  & $PyVenv -m pip install --quiet -r requirements.txt
}

# Ensure analysis deps are present (aiohttp+segno serve the app; numpy/scipy/opencv
# power the per-recording sync reports). Cheap import check; only installs if missing.
& $PyVenv -c "import aiohttp, segno, numpy, scipy, cv2" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "[run] installing/updating Python dependencies..."
  & $PyVenv -m pip install --quiet --upgrade pip
  & $PyVenv -m pip install --quiet -r requirements.txt
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "[run] WARNING: ffmpeg not found on PATH - sync/reports need it (winget install Gyan.FFmpeg)."
}

# Determine the port (default 8443), then stop any previous instance bound to it.
# A stale server still holding the port is the usual reason a 'restart' doesn't take
# effect - the browser keeps hitting old code.
$Port = 8443
for ($i = 0; $i -lt $args.Count - 1; $i++) {
  if ($args[$i] -eq "--port") { $Port = [int]$args[$i + 1] }
}
try {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    Write-Host "[run] stopping previous instance on port $Port (pid $($c.OwningProcess))..."
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  if ($conns) { Start-Sleep -Seconds 1 }
} catch { }

# cert check (iOS needs HTTPS for the camera). app.py auto-creates the cert with
# mkcert if it is on PATH; otherwise it serves HTTP (fine only for localhost testing).
if (-not ((Test-Path "certs\cert.pem") -and (Test-Path "certs\key.pem"))) {
  if (Get-Command mkcert -ErrorAction SilentlyContinue) {
    Write-Host "[run] No TLS cert yet - app.py will create one with mkcert on startup."
  } else {
    Write-Host "[run] No TLS cert and mkcert not installed. iOS will NOT allow the camera over HTTP."
    Write-Host "[run] Install mkcert once:  winget install FiloSottile.mkcert  (then 'mkcert -install')."
  }
}

& $PyVenv app.py --open @args
