# Packaging (Windows)

## Goals

- Double-click **OpenPVScope Setup.exe**
- Start Menu + desktop shortcut
- Register **`.opsx`** file association
- Bundle app + (eventually) OpenSfM conda env
- No Docker for end users

## Build outline

1. `cd frontend && npm ci && npm run build`  
2. Copy `frontend/dist` → `backend/openpvscope/static`  
3. Freeze backend with PyInstaller (`packaging/windows/openpvscope.spec`)  
4. Inno Setup script (`packaging/windows/OpenPVScope.iss`) produces the installer  

OpenSfM: ship a conda environment via [constructor](https://github.com/conda/constructor) or document a companion engine folder under `engines/opensfm/`.

## Dev vs release

| Audience | How to run |
|----------|------------|
| Developer | `uvicorn` + `npm run dev` or `python -m openpvscope.desktop` |
| End user | Installed `.exe` from Setup |

See `packaging/windows/` for scripts and the Inno Setup template.
