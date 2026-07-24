# Fetch latest ODX_Setup_*.exe into packaging/windows/vendor (developer/bootstrap use).
# Not committed to git (large binary). End users install ODX from the OpenPVScope UI.

param(
  [string]$OutDir = "",
  [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
if (-not $OutDir) {
  $OutDir = Join-Path $RepoRoot "packaging\windows\vendor"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$api = "https://api.github.com/repos/WebODM/ODX/releases/latest"
Write-Host "Fetching release metadata: $api"
$rel = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "OpenPVScope-fetch-odx" }
if ($Version) {
  $api = "https://api.github.com/repos/WebODM/ODX/releases/tags/v$Version"
  if (-not $Version.StartsWith("v")) {
    $api = "https://api.github.com/repos/WebODM/ODX/releases/tags/$Version"
  }
  $rel = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "OpenPVScope-fetch-odx" }
}

$asset = $rel.assets | Where-Object { $_.name -match '^ODX_Setup_.*\.exe$' } | Select-Object -First 1
if (-not $asset) {
  throw "No ODX_Setup_*.exe asset on release $($rel.tag_name)"
}

$dest = Join-Path $OutDir $asset.name
Write-Host "Downloading $($asset.name) ($([math]::Round($asset.size/1MB,1)) MB) -> $dest"
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $dest -UseBasicParsing
Write-Host "Done: $dest"
Write-Host "Developers: run the setup, or use .\scripts\bootstrap_odx.ps1. End users install ODX from the app UI."
