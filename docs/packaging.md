# Packaging (Windows)

## Goals

- Double-click **OpenPVScope Setup.exe**
- Start Menu + desktop shortcut
- Register **`.opsx`** file association
- Bundle app + DJI SDK when present
- Chain-install **ODX** (photogrammetry) by default — no Docker, no conda, no Visual Studio for end users

## Build outline

Run from repo root:

```bat
packaging\windows\build.bat
```

That script:

1. Downloads ODX companion installer via `scripts\fetch_odx_setup.ps1` → `packaging/windows/vendor/ODX_Setup_*.exe` (**fails if missing**)
2. Builds frontend (`npm ci` + `npm run build`)
3. Copies `frontend/dist` → `backend/openpvscope/static`
4. Freezes backend with PyInstaller (`packaging/windows/openpvscope.spec`)
5. Prints next step: compile Inno Setup

Then compile `packaging/windows/OpenPVScope.iss` with Inno Setup. The `odx` component **requires** `vendor\ODX_Setup*.exe` (compile fails if absent).

### Components

| Component | Default (Full) | Notes |
|-----------|----------------|--------|
| Application | yes | PyInstaller tree |
| ODX photogrammetry | yes | Silent `ODX_Setup_*.exe` `/VERYSILENT /DIR=C:\ODX`; verifies `C:\ODX\run.bat` |
| DJI Thermal SDK | yes when present | `engines/dji_tsdk` |

**Full** Setup installs the app and ODX automatically. **Compact** installs the app only (GeoTIFF skip path); user can install ODX later from [WebODM/ODX releases](https://github.com/WebODM/ODX/releases).

At runtime the app resolves ODX via `OPENPVSCOPE_ODX_ROOT`, then `C:\ODX`, registry InstallLocation, and `run.bat` on PATH.

**License:** ODX is AGPL-3.0. OpenPVScope calls it as a separate process; see `packaging/windows/vendor/ODX_AGPL_NOTICE.txt`.

## Dev vs release

| Audience | How to run |
|----------|------------|
| Developer | `uvicorn` + `npm run dev` or `python -m openpvscope.desktop`; install ODX once with `.\scripts\bootstrap_odx.ps1` |
| End user | Installed `.exe` from Setup (**Full** includes ODX automatically) |

See `packaging/windows/` for scripts and the Inno Setup template.
