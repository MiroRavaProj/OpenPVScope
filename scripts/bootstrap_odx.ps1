# Download and install native ODX (WebODM engine) for OpenPVScope photogrammetry.
#
#   .\scripts\bootstrap_odx.ps1
#   .\scripts\bootstrap_odx.ps1 -SkipInstall   # download only to packaging/windows/vendor
#   .\scripts\bootstrap_odx.ps1 -Dir D:\ODX
#
# After install, health should report odx.available and run.bat under the install dir
# (default C:\ODX). Set OPENPVSCOPE_ODX_ROOT if you used a custom directory.

param(
  [string]$Dir = "C:\ODX",
  [switch]$SkipInstall,
  [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$VendorDir = Join-Path $RepoRoot "packaging\windows\vendor"
$LogPath = Join-Path $RepoRoot "engines\_odx_bootstrap.log"

function Write-Info([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Warn([string]$m) { Write-Host "WARN: $m" -ForegroundColor Yellow }
function Write-Err([string]$m) { Write-Host "ERR: $m" -ForegroundColor Red }

Start-Transcript -Path $LogPath -Force | Out-Null
try {
  if (Test-Path (Join-Path $Dir "run.bat")) {
    Write-Info "ODX already present at $Dir\run.bat"
    Write-Host "    Set OPENPVSCOPE_ODX_ROOT=$Dir if health does not pick it up."
    return
  }

  Write-Info "Fetching ODX Setup into $VendorDir"
  $fetchArgs = @{ OutDir = $VendorDir }
  if ($Version) { $fetchArgs.Version = $Version }
  & (Join-Path $ScriptDir "fetch_odx_setup.ps1") @fetchArgs

  $setup = Get-ChildItem $VendorDir -Filter "ODX_Setup*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $setup) {
    throw "ODX_Setup*.exe not found under $VendorDir after fetch"
  }
  Write-Info "Setup: $($setup.FullName)"

  if ($SkipInstall) {
    Write-Info "SkipInstall set - not running the installer."
    return
  }

  Write-Info "Installing ODX silently to $Dir (this may take several minutes)..."
  $p = Start-Process -FilePath $setup.FullName -ArgumentList @(
    "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/DIR=$Dir"
  ) -Wait -PassThru
  if ($p.ExitCode -ne 0) {
    throw "ODX setup exit code $($p.ExitCode). Try running $($setup.Name) interactively."
  }

  if (-not (Test-Path (Join-Path $Dir "run.bat"))) {
    throw "Install finished but $Dir\run.bat is missing"
  }

  Write-Info "ODX ready: $Dir\run.bat"
  Write-Host ('    Optional: $env:OPENPVSCOPE_ODX_ROOT = ''' + $Dir + '''')
  Write-Host "    Restart OpenPVScope and check /api/health odx.available"
}
catch {
  Write-Err $_
  throw
}
finally {
  Stop-Transcript | Out-Null
  Write-Host "Log: $LogPath"
}
