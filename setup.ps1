# One-shot environment setup for Windows (PowerShell).
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Creates a virtualenv, installs Python deps, and checks external tools.
# NOTE: the host is designed for macOS; Windows works for the host process,
# but mkcert cert trust on iPhones and the start.command launcher are macOS-oriented.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== camera time sync :: setup =="

# 1. Python 3.10+
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Error "python not found. Install Python 3.10+ from https://www.python.org/downloads/"; exit 1 }
Write-Host "[1/3] $(python --version)"

# 2. venv + dependencies
Write-Host "[2/3] Creating .venv and installing dependencies..."
python -m venv .venv
.\.venv\Scripts\python -m pip install --quiet --upgrade pip
.\.venv\Scripts\python -m pip install --quiet -r requirements.txt
Write-Host "      Python dependencies installed."

# 3. external tools
Write-Host "[3/3] Checking external tools..."
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Write-Host "      ffmpeg  OK" }
else { Write-Host "      ffmpeg  MISSING - install from https://ffmpeg.org/download.html (add to PATH)" }
if (Get-Command mkcert -ErrorAction SilentlyContinue) { Write-Host "      mkcert  OK" }
else { Write-Host "      mkcert  MISSING - 'choco install mkcert' then 'mkcert -install'" }

Write-Host ""
Write-Host "Setup complete. Start the host with:  .\.venv\Scripts\python app.py"
