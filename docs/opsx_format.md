# `.opsx` project format

An **`.opsx`** file is a ZIP archive (like `.docx` / `.qgz`). OpenPVScope is the default app for this extension.

## Layout

```text
MyPlant.opsx
  manifest.json                 # format_version, name, ids, timestamps
  workflow.json                 # per-step status
  inputs/
    raw/rgb/
    raw/thermal/
    ortho/rgb.tif
    ortho/thermal.tif
    ortho/thermal_aligned.tif
  photogrammetry/
    rgb_job.json
    thermal_job.json
    rgb/                        # OpenSfM dataset (optional, large)
    thermal/
  alignment/
    gcps.json
    transform.json
  detection/
    rgb/aoi.geojson
    rgb/panels.geojson
    thermal/panels.geojson
  segmentation/
    pairs.jsonl
    panels/<uuid>/rgb.png
    panels/<uuid>/thermal.tif
    panels/<uuid>/features.json
  labels/
    labels.csv
  models/
  classification/
    results.geojson
  exports/
```

## Rules

- `manifest.format_version` is an integer; bump when breaking layout.
- No UI widget state in the package.
- No base64 rasters inside JSON.
- Large GeoTIFFs use ZIP **store** (no recompression).
- Each pipeline stage owns its folder.

## `manifest.json` (example)

```json
{
  "format_version": 1,
  "name": "Colleferro North",
  "created_at": "2026-07-23T16:00:00Z",
  "updated_at": "2026-07-23T16:00:00Z",
  "app": "OpenPVScope"
}
```

## `workflow.json` (example)

```json
{
  "photogrammetry": { "status": "done", "skipped": false },
  "alignment": { "status": "pending" },
  "detection": { "status": "pending" },
  "segmentation": { "status": "pending" },
  "models": { "status": "pending" },
  "classification": { "status": "pending" },
  "outputs": { "status": "pending" }
}
```
