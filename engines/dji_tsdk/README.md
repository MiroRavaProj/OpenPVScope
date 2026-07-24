# DJI Thermal SDK bundle (`dji_tsdk`)

Headless R-JPEG → float32 TIFF conversion for OpenPVScope uses DJI's Thermal SDK
(`libdirp.dll`) plus optional ExifTool metadata copy and an MLP parametric fallback.

## Layout

Place these files under this folder (copied from the author's DJI Image Processor tool):

| Path | Purpose |
|------|---------|
| `libdirp.dll` + companion DLLs / `.lib` / `libv_list.ini` | DJI Thermal SDK (DIRP) |
| `exiftool.exe` + `exiftool_files/` | Optional GPS/EXIF copy onto output TIFFs |
| `thermal_data/parametric_mlp_v1.npz` (+ `*_meta.json`) | Optional MLP when `parametric_fallback=True` |

## Discovery order

1. Environment variable `OPENPVSCOPE_DJI_TSDK` (directory containing `libdirp.dll`, or a parent that has `DJI_TSDK/libdirp.dll`)
2. `engines/dji_tsdk` next to the repo root / frozen app (`{app}/engines/dji_tsdk`)
3. Otherwise conversion raises a clear "SDK not found" error

Probe from Python:

```python
from openpvscope.thermal import probe_dji_sdk
print(probe_dji_sdk())  # {"available": bool, "path": ..., "error": ...}
```

## License / redistribution

The DJI Thermal SDK is proprietary. Redistribute only under DJI's license terms.
ExifTool is separate (Phil Harvey / GPL-compatible packaging for Windows).
Do not commit large binaries to git if your policy forbids it; ship them with the
Windows installer (`engines/dji_tsdk/*` via Inno Setup) or document a local copy step.
