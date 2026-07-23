# OpenSfM integration

OpenPVScope uses **[OpenSfM](https://github.com/OpenSfM/OpenSfM) 1.0** as the primary photogrammetry engine.

## Why OpenSfM 1.0

- Conda-based Windows build ([building.md](https://github.com/OpenSfM/OpenSfM/blob/master/doc/building.md)) — no separate Visual Studio install.
- Dense pipeline produces georeferenced **`ortho.tif`** ([dense.md](https://github.com/OpenSfM/OpenSfM/blob/master/doc/dense.md)).

## Command sequence (per modality)

```text
extract_metadata
detect_features
match_features
create_tracks
reconstruct
undistort
dense_clustering
compute_depthmaps      # requires OpenCL GPU
fuse_depthmaps
dense_merging --georeferenced
```

Result: `undistorted/depthmaps/ortho.tif` → copied into `.opsx` as `inputs/ortho/rgb.tif` or `thermal.tif`.

## Thermal ingest

- EXIF / GPS assumed present on thermal frames.
- **TIFF** → used as-is.
- **DJI proprietary** → converted via `openpvscope.thermal.dji.convert_dji_thermal` (hook; converter supplied separately).

## GPU / OpenCL

Dense stages need OpenCL. Intel/AMD integrated GPUs usually work with vendor drivers. OpenPVScope probes OpenCL before starting photogrammetry.

## Locating the engine

1. Env `OPENPVSCOPE_OPENSFM_ROOT`  
2. `engines/opensfm/` next to the app  
3. `opensfm` / `opensfm.bat` on `PATH`

## ODX fallback

Optional later: Advanced setting to call native ODX instead. Not required for the default path.
