# OpenPVScope

**OpenPVScope** is an open-source desktop application for end-to-end solar PV inspection: from drone RGB + thermal photos to georeferenced orthophotos, panel detection, thermal analysis, and anomaly classification.

It is a ground-up rewrite of the *Solar PV Anomaly Detection Suite* developed for a master’s thesis on UAV thermography of PV plants.

## What it does

A guided pipeline:

1. **Photogrammetry** — build RGB and thermal orthophotos with [OpenSfM](https://github.com/OpenSfM/OpenSfM) 1.0  
2. **Alignment** — 4-point co-registration of thermal → RGB (metadata-only georef rewrite)  
3. **Detection** — panel localization  
4. **Segmentation** — RGB↔thermal pairing and thermal features  
5. **Models / Classification** — train and apply anomaly models  
6. **Outputs** — export results  

Projects are saved as a single **`.opsx`** file (zip package). You can skip photogrammetry if you already have GeoTIFF orthophotos.

## For users (Windows)

1. Download the latest **OpenPVScope Setup** from [Releases](../../releases) (when published).  
2. Install and open **OpenPVScope** from the Start Menu.  
3. Create a project → follow the pipeline wizard.  
4. Double-click any `.opsx` file to reopen it.

**Requirements:** Windows 10+, and for photogrammetry a GPU with **OpenCL** (Intel/AMD integrated graphics usually work with current drivers). Dense OpenSfM stages need OpenCL; see [docs/opensfm.md](docs/opensfm.md).

## For developers

```bash
# Backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Frontend
cd ../frontend
npm install
npm run dev
```

In another terminal:

```bash
cd backend
uvicorn openpvscope.api.app:app --reload --port 8787
```

Or run the desktop shell (API + window):

```bash
cd backend
python -m openpvscope.desktop
```

See [docs/architecture.md](docs/architecture.md), [docs/opsx_format.md](docs/opsx_format.md), and [docs/packaging.md](docs/packaging.md).

### OpenSfM (optional for photogrammetry)

Follow [OpenSfM building.md](https://github.com/OpenSfM/OpenSfM/blob/master/doc/building.md) (conda lockfile). Set `OPENPVSCOPE_OPENSFM_ROOT` to the OpenSfM repo root, or place an env under `engines/opensfm/`.

## License

MIT — see [LICENSE](LICENSE).  
OpenSfM and related engines have their own licenses; see attributions when bundling.

## Citation

If you use OpenPVScope in academic work, please cite the related thesis on UAV thermal anomaly detection for PV plants (Miro Rava).
