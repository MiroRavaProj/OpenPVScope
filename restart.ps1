# OpenPVScope restart / redeploy
# Usage:
#   .\restart.ps1
#   .\restart.ps1 -ShowConsole
#   .\restart.ps1 -Dev          # uvicorn --reload (dev only; can leave orphan workers)

param(
  [switch]$ShowConsole,
  [switch]$Dev
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Static = Join-Path $Backend "openpvscope\static"
$Port = 8787

Write-Host "==> OpenPVScope restart (Python 3.13 venv)" -ForegroundColor Cyan

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

Write-Host "==> Optional C++ spatial NMS (skipped if no MSVC; Numba is preferred)..."
$NativeNms = Join-Path $Backend "native_nms"
Push-Location $NativeNms
try {
  & $Py -m pip install -q setuptools wheel "pybind11>=2.12" 2>$null | Out-Null
  & $Py setup.py build_ext --inplace 2>$null | Out-Null
  Get-ChildItem -Path $NativeNms -Filter "_spatial_nms*" -ErrorAction SilentlyContinue |
    ForEach-Object {
      Copy-Item $_.FullName (Join-Path $Backend "openpvscope\detection\$($_.Name)") -Force
    }
} catch {
  # Numba path covers production; C++ is optional
} finally {
  Pop-Location
}
$nmsBackend = & $Py -c "from openpvscope.detection.spatial_nms_fast import numba_available, warmup_numba; from openpvscope.detection.template_match import _load_cpp_nms; warmup_numba(); print('numba' if numba_available() else ('cpp' if _load_cpp_nms() else 'python'))"
Write-Host "==> Spatial NMS backend: $nmsBackend" -ForegroundColor Cyan

function Stop-OpenPVScopeServers {
  param([int]$Port)

  Write-Host "==> Stopping all OpenPVScope / :$Port processes..." -ForegroundColor Yellow
  $killed = @{}

  function Kill-Tree([int]$ProcessId) {
    if ($ProcessId -le 0 -or $killed.ContainsKey($ProcessId)) { return }
    $killed[$ProcessId] = $true
    # /T = kill child processes too (critical for uvicorn --reload workers)
    # Prefer cmd redirect so stderr from already-dead PIDs does not trip $ErrorActionPreference Stop
    cmd.exe /c "taskkill /F /T /PID $ProcessId >nul 2>&1" | Out-Null
  }

  # 1) Anyone holding the port (any state)
  Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    ForEach-Object { Kill-Tree $_.OwningProcess }

  # 2) Command-line match for this app
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -match '^(python|uvicorn)(\.exe)?$' -and
      $_.CommandLine -and
      (
        $_.CommandLine -match "openpvscope\.api\.app" -or
        $_.CommandLine -match "--port\s+$Port" -or
        $_.CommandLine -match [regex]::Escape($Backend) -or
        ($_.CommandLine -match "uvicorn" -and $_.CommandLine -match "openpvscope")
      )
    } |
    ForEach-Object { Kill-Tree $_.ProcessId }

  # 3) Orphan multiprocessing workers whose parent is gone / was ours
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and
      $_.CommandLine -match "multiprocessing\.spawn" -and
      $_.CommandLine -match "spawn_main"
    } |
    ForEach-Object {
      $parentAlive = $null -ne (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue)
      if (-not $parentAlive -or $killed.ContainsKey([int]$_.ParentProcessId)) {
        Kill-Tree $_.ProcessId
      }
    }

  Start-Sleep -Milliseconds 500

  # 4) Wait until port is free
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $deadline) {
    $still = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($still.Count -eq 0) { break }
    foreach ($c in $still) { Kill-Tree $c.OwningProcess }
    Start-Sleep -Milliseconds 400
  }

  $left = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
  if ($left.Count -gt 0) {
    Write-Host "==> WARNING: port $Port still in use by PID(s): $($left.OwningProcess -join ', ')" -ForegroundColor Red
    throw "Cannot bind :$Port - kill remaining PID(s) manually: $($left.OwningProcess -join ', ')"
  }
  Write-Host "==> Port $Port is free" -ForegroundColor Green
}

Stop-OpenPVScopeServers -Port $Port

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
Write-Host "==> Static UI -> backend/openpvscope/static"

Write-Host "==> Starting uvicorn on http://127.0.0.1:$Port (background)..."
$env:PYTHONPATH = $Backend
$winStyle = if ($ShowConsole) { "Normal" } else { "Hidden" }

# Default: no --reload (avoids orphan Windows reloader/worker trees).
# Use -Dev for auto-reload during local coding.
$uvArgs = @(
  "-m", "uvicorn",
  "openpvscope.api.app:app",
  "--host", "127.0.0.1",
  "--port", "$Port",
  "--no-access-log"
)
if ($Dev) {
  $uvArgs += @("--reload", "--reload-dir", $Backend)
  Write-Host "==> Dev reload enabled" -ForegroundColor Yellow
}

Start-Process -FilePath $Py -ArgumentList $uvArgs -WorkingDirectory $Backend -WindowStyle $winStyle

Start-Sleep -Seconds 1.5
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 8
  Write-Host "==> Health: $($r.StatusCode) $($r.Content)" -ForegroundColor Green
} catch {
  Write-Host "==> Server starting (health not ready yet): $_" -ForegroundColor Yellow
}

# Prove which pipeline revision the live process imports
try {
  $probe = & $Py -c "from openpvscope.detection.pipeline import PIPELINE_REV; print(PIPELINE_REV)"
  Write-Host "==> Pipeline rev: $probe" -ForegroundColor Cyan
} catch {
  Write-Host "==> Could not probe PIPELINE_REV: $_" -ForegroundColor Yellow
}

# Optional ODX tip (do not block app start)
$odxRoot = $env:OPENPVSCOPE_ODX_ROOT
$odxOk = $false
if ($odxRoot -and (Test-Path (Join-Path $odxRoot "run.bat"))) {
  $odxOk = $true
} elseif (Test-Path "C:\ODX\run.bat") {
  $odxOk = $true
}

Write-Host ""
Write-Host "Open: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "Venv: $Venv (Python $ver)"
if (-not $odxOk) {
  Write-Host "Tip: ODX not found (C:\ODX\run.bat or OPENPVSCOPE_ODX_ROOT)." -ForegroundColor DarkGray
  Write-Host "     End users: re-run OpenPVScope Full Setup. Developers: .\scripts\bootstrap_odx.ps1" -ForegroundColor DarkGray
}
if (-not $Dev) {
  Write-Host "Tip: use .\restart.ps1 -Dev only when you need --reload" -ForegroundColor DarkGray
}
