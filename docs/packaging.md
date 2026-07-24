# Packaging (Windows)

## Goals

- Double-click **OpenPVScope Setup.exe**
- Start Menu + desktop shortcut
- Register **`.opsx`** file association
- Bundle app + DJI SDK when present
- **ODX is not bundled** — the app detects an existing install or downloads/installs ODX on demand from the Photogrammetry UI (no Docker, no conda, no Visual Studio for end users)

## Build outline

Run from repo root:

```bat
packaging\windows\build.bat
```

That script:

1. Builds frontend (`npm ci` + `npm run build`)
2. Copies `frontend/dist` → `backend/openpvscope/static`
3. Freezes backend with PyInstaller (`packaging/windows/openpvscope.spec`)
4. Prints next step: compile Inno Setup

Then compile `packaging/windows/OpenPVScope.iss` with Inno Setup.

### Components

| Component | Notes |
|-----------|--------|
| Application | PyInstaller tree |
| DJI Thermal SDK | when `engines/dji_tsdk` present |
| ODX AGPL notice | copied to `{app}\licenses` |

At runtime the app resolves ODX via `OPENPVSCOPE_ODX_ROOT`, then `C:\ODX`, registry, and `run.bat` on PATH. If missing, the UI offers **Install ODX** (downloads latest `ODX_Setup` from GitHub and installs silently to `C:\ODX`) or **Continue with GeoTIFFs only**.

**License:** ODX is AGPL-3.0. OpenPVScope calls it as a separate process; see `packaging/windows/vendor/ODX_AGPL_NOTICE.txt`.

## Dev vs release

| Audience | How to run |
|----------|------------|
| Developer | `uvicorn` + `npm run dev` or `python -m openpvscope.desktop`; optional `.\scripts\bootstrap_odx.ps1` |
| End user | Installed `.exe`; install ODX from Photogrammetry when needed |

See `packaging/windows/` for scripts and the Inno Setup template.
