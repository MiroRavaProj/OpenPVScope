# OpenPVScope restart / redeploy
# Usage (from anywhere):
#   D:\AAA_TESI\OpenPVScope\restart.cmd
#   powershell -File D:\AAA_TESI\OpenPVScope\restart.ps1
# From repo root:
#   .\restart.ps1
#   .\restart.ps1 -ShowConsole   # show uvicorn window for debugging

param(
  [switch]$ShowConsole
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Uvicorn = Join-Path $Venv "Scripts\uvicorn.exe"
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Static = Join-Path $Backend "openpvscope\static"
$Port = 8787

Write-Host "==> OpenPVScope restart (Python 3.13 venv)" -ForegroundColor Cyan

# --- ensure venv ---
if (-not (Test-Path $Py)) {
  Write-Host "==> Creating .venv with Python 3.13..."
  py -3.13 -m venv $Venv
}
$ver = & $Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($ver -ne "3.13") {
  throw "Expected Python 3.13 in .venv, got $ver. Delete .venv and re-run."
}

Write-Host "==> Installing backend (editable)..."
& $Py -m pip install -q -U pip
& $Py -m pip install -q -e "$Backend[dev,desktop]"

# --- kill listeners on port ---
Write-Host "==> Stopping process(es) on :$Port..."
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
  try {
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
  } catch {}
}
# also stop stray uvicorn/python for this app
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='uvicorn.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match "openpvscope|8787") } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
  }
Start-Sleep -Milliseconds 600

# --- frontend build + static ---
Write-Host "==> Building frontend..."
Push-Location $Frontend
try {
  if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    npm install
  }
  npm run build
} finally {
  Pop-Location
}

if (Test-Path $Static) {
  Remove-Item -Recurse -Force $Static
}
Copy-Item -Recurse (Join-Path $Frontend "dist") $Static
Write-Host "==> Static UI → backend/openpvscope/static"

# --- start API (hidden console window; access log quiet) ---
Write-Host "==> Starting uvicorn on http://127.0.0.1:$Port (background)..."
$env:PYTHONPATH = $Backend
# Hidden window: the API must keep running; you don't need to see uvicorn logs.
# Use -ShowConsole to debug:  .\restart.ps1 -ShowConsole
$winStyle = if ($ShowConsole) { "Normal" } else { "Hidden" }
Start-Process -FilePath $Py -ArgumentList @(
  "-m", "uvicorn",
  "openpvscope.api.app:app",
  "--host", "127.0.0.1",
  "--port", "$Port",
  "--reload",
  "--reload-dir", $Backend,
  "--no-access-log"
) -WorkingDirectory $Backend -WindowStyle $winStyle

Start-Sleep -Seconds 1.2
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 5
  Write-Host "==> Health: $($r.StatusCode) $($r.Content)" -ForegroundColor Green
} catch {
  Write-Host "==> Server starting (health not ready yet): $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Open: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Venv: $Venv (Python $ver)"
