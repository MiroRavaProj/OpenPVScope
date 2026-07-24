# OpenPVScope

**OpenPVScope** is an open-source desktop application for end-to-end solar PV inspection: from drone RGB + thermal photos to georeferenced orthophotos, panel detection, thermal analysis, and anomaly classification.

It is a ground-up rewrite of the earlier *Solar PV Anomaly Detection Suite* for UAV thermography of PV plants.

## What it does

A guided pipeline:

1. **Photogrammetry** — build RGB and thermal orthophotos with [OpenSfM](https://github.com/OpenSfM/OpenSfM) 1.0  
2. **Alignment** — 4-point co-registration of thermal → RGB (metadata-only georef rewrite)  
3. **Detection** — panel localization  
4. **Segmentation** — RGB↔thermal pairing and thermal features  
5. **Models / Classification** — train and apply anomaly models  
6. **Outputs** — export results  

Projects live on disk as a folder plus a **`.opsx`** JSON descriptor (always autosaved). Use **`.opsz`** only when exporting a portable zip of the whole project. You can skip photogrammetry if you already have GeoTIFF orthophotos.

## For users (Windows)

1. Download the latest **OpenPVScope Setup** from [Releases](../../releases) (when published).  
2. Install and open **OpenPVScope** from the Start Menu.  
3. On first screen: **Create** a project (name + save folder) or **Open** an existing `.opsx`.  
4. Work normally — everything autosaves; reopen the `.opsx` after a crash to continue.  
5. **Export .opsz** when you need a portable archive to share or back up.

**Requirements:** Windows 10+, and for photogrammetry a GPU with **OpenCL** (Intel/AMD integrated graphics usually work with current drivers). Dense OpenSfM stages need OpenCL; see [docs/opensfm.md](docs/opensfm.md).

## For developers

**One-line restart / redeploy** (builds UI, uses project `.venv` with **Python 3.13**, restarts API on port 8787):

```bat
D:\AAA_TESI\OpenPVScope\restart.cmd
```

Or from the repo root: `.\restart.ps1`

First run creates `.venv` with `py -3.13` and installs the backend. Open http://127.0.0.1:8787 after it finishes.

Manual setup (same venv):

```bash
cd OpenPVScope
py -3.13 -m venv .venv
.\.venv\Scripts\activate
pip install -e "./backend[dev,desktop]"

cd frontend
npm install
npm run dev
```

In another terminal (always use the project venv, not global Python):

```bash
cd OpenPVScope\backend
..\..\.venv\Scripts\uvicorn openpvscope.api.app:app --reload --port 8787
```

Or desktop shell:

```bash
.\.venv\Scripts\python -m openpvscope.desktop
```

See [docs/architecture.md](docs/architecture.md), [docs/opsx_format.md](docs/opsx_format.md), and [docs/packaging.md](docs/packaging.md).

### OpenSfM (optional for photogrammetry)

Follow [OpenSfM building.md](https://github.com/OpenSfM/OpenSfM/blob/master/doc/building.md) (conda lockfile). Set `OPENPVSCOPE_OPENSFM_ROOT` to the OpenSfM repo root, or place an env under `engines/opensfm/`.

## License

MIT — see [LICENSE](LICENSE).  
OpenSfM and related engines have their own licenses; see attributions when bundling.

## Citation

If you use OpenPVScope in academic work, please cite the related publication on UAV thermal anomaly detection for PV plants (Miro Rava).
