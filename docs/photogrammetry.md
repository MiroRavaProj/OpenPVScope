# Photogrammetry (ODX)

OpenPVScope uses native **[ODX](https://github.com/WebODM/ODX)** (WebODM engine, includes OpenSfM 1.0) to turn raw drone frames into georeferenced orthophotos.

## Why ODX

- Official **Windows Setup** (`ODX_Setup_*.exe` ~227 MB) — prebuilt, no MSVC/conda on the user machine.
- Output: `odm_orthophoto/odm_orthophoto.tif`.
- GPU acceleration on by default on Windows.
- OpenPVScope **detects** an existing install, or offers **in-app install** (download latest release → silent install to `C:\ODX`).

## Pipeline (per modality)

1. Upload raw images → `inputs/raw/{rgb|thermal}/`
2. Prepare dataset → `photogrammetry/{modality}/images/` (DJI R-JPEG converted via Thermal SDK when needed)
3. Run `run.bat <dataset>` (ODX)
4. Copy `odm_orthophoto/odm_orthophoto.tif` → `inputs/ortho/{modality}.tif`

## Thermal ingest

- EXIF / GPS assumed present on thermal frames.
- **TIFF** → used as-is.
- **DJI proprietary (R-JPEG)** → converted via `openpvscope.thermal.dji` (requires `engines/dji_tsdk/` or `OPENPVSCOPE_DJI_TSDK`).

## Locating ODX

Resolution order in `find_odx_root()`:

1. Env `OPENPVSCOPE_ODX_ROOT`
2. `C:\ODX` (ODX Setup / in-app install default)
3. Program Files / LocalAppData / home `ODX` folders
4. Uninstall registry InstallLocation for display name containing “ODX”
5. `engines/odx` next to a frozen exe (unusual)
6. `run.bat` / `winrun.bat` on `PATH`

### End users

1. If ODX is already installed → OpenPVScope uses it.
2. If not → a prompt explains photogrammetry needs ODX; choose **Install ODX** or **Continue with GeoTIFFs only**.
3. Skipping greys out ODX process controls; import GeoTIFFs remains available; **Install ODX** stays on the Photogrammetry screen.

### Developers

```powershell
.\scripts\bootstrap_odx.ps1
```

Or install from [ODX Releases](https://github.com/WebODM/ODX/releases). Optionally set `OPENPVSCOPE_ODX_ROOT`. Missing ODX does not prevent the rest of the app from starting.

## ODX license

AGPL-3.0. OpenPVScope invokes ODX as a separate installed program.
