# Project formats: `.opsx` (live) and `.opsz` (portable)

## Mental model

| Format | Role |
|--------|------|
| **`.opsx`** | Live project **descriptor** (JSON). Sits inside a project folder and **references** data folders beside it. Always autosaved. |
| **`.opsz`** | **Export archive** (ZIP of the whole project folder). Use to share or backup. Import extracts then opens. |

There is **no silent cache folder** for new projects. Creating a project requires choosing a parent directory on disk.

## Live project layout

```text
C:\Projects\Colleferro_North\          ← user-chosen location
  Colleferro_North.opsx                ← JSON descriptor (autosaved)
  manifest.json                        ← sidecar copy
  workflow.json                        ← sidecar copy
  inputs/
    raw/rgb/
    raw/thermal/
    ortho/rgb.tif
    ortho/thermal.tif
    ortho/thermal_aligned.tif
  photogrammetry/
  alignment/
    gcps.json
    transform.json
  detection/
    rgb/
      aoi.geojson
      aoi_ring.json
      grid.geojson
      grid_meta.json
      panels.geojson
      detection_meta.json
    thermal/
      aoi.geojson
      aoi_ring.json
      grid.geojson
      grid_meta.json
      panels.geojson
      detection_meta.json
  segmentation/
    pairs.json
    pairs.geojson
    panels/<id>/{rgb.png,thermal.png,thermal.tif,meta.json}
  labels/
  models/
  classification/
  exports/
  work/
```

## `.opsx` JSON (format_version 2)

```json
{
  "format_version": 2,
  "kind": "openpvscope-project",
  "app": "OpenPVScope",
  "name": "Colleferro North",
  "id": "0500c34dfed5",
  "root": ".",
  "paths": {
    "inputs": "inputs",
    "alignment": "alignment",
    "workflow": "workflow.json"
  },
  "manifest": { "...": "..." },
  "workflow": { "...": "..." }
}
```

Paths are **relative** to the folder that contains the `.opsx`, so moving/renaming the whole folder keeps the project valid.

## Autosave

Every meaningful change (workflow step, alignment GCPs, orthophoto import, …) writes data files into the project folder and rewrites the `.opsx` descriptor using **atomic writes** (temp file + replace). Closing or crashing does not require a manual Save for recovery: reopen the `.opsx`.

## Undo / redo

Project changes that affect tracked files (workflow, manifest, alignment JSON, and orthophoto GeoTIFFs by default) are checkpointed into `.openpvscope_history/` using **content-addressable storage** (git-style): each unique file is stored once under `objects/` by hash; snapshots are tiny JSON manifests. Unchanged rasters are not duplicated.

**GC** removes object blobs that no snapshot references anymore (after undo trim / redo discard).

**Hardlinks** (same bytes, two names on NTFS) are used when restoring small JSON sidecars for speed; rasters are always copied on restore so in-place GeoTIFF writes cannot corrupt the object store.

Use **← / →** or **Ctrl+Z / Ctrl+Y**. Depth and whether rasters are included are controlled in **Settings**.

## `.opsz` export / import

- **Export full:** zip project data (undo history folder is always excluded)
- **Export light:** also skip rebuildable prefixes (default `work/`, `photogrammetry/`) — configurable in Settings
- **Import:** choose `.opsz` + destination folder → extract → open the embedded `.opsx`

Large rasters are stored uncompressed inside the zip (ZIP_STORED) to avoid recompression cost.
