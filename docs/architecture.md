# Architecture

## Overview

```
┌─────────────────────────────────────────────┐
│  Desktop shell (pywebview)                  │
│  ┌───────────────────────────────────────┐  │
│  │  React + MapLibre (static UI)         │  │
│  └──────────────────┬────────────────────┘  │
│                     │ HTTP / WS             │
│  ┌──────────────────▼────────────────────┐  │
│  │  FastAPI (openpvscope.api)            │  │
│  │  project / ingest / photogrammetry…   │  │
│  └──────────────────┬────────────────────┘  │
│                     │                       │
│  ┌──────────────────▼────────────────────┐  │
│  │  Pure Python core (no UI imports)     │  │
│  │  .opsx pack/unpack · alignment · …    │  │
│  └──────────────────┬────────────────────┘  │
│                     │ subprocess            │
│  ┌──────────────────▼────────────────────┐  │
│  │  ODX (native Windows install)         │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

## Design principles

- **Guided pipeline** — new projects start at Photogrammetry; skip to GeoTIFF import is explicit.
- **UI is a client** — business logic lives in `openpvscope/` with no React/NiceGUI imports.
- **`.opsx` projects** — staged zip package; never one mega-JSON of all panels.
- **Self-contained Windows install** — no Docker for end users.

## Pipeline stages

| Step | Module | Writes into project folder |
|------|--------|----------------------------|
| Photogrammetry | `openpvscope.photogrammetry` (ODX) | `inputs/ortho/`, `photogrammetry/` |
| Alignment | `openpvscope.alignment` | `alignment/`, `thermal_aligned.tif` |
| Detection | `openpvscope.detection` | `detection/rgb|thermal/{aoi,grid,panels}.geojson` |
| Segmentation | `openpvscope.segmentation` | `segmentation/pairs.json`, `panels/<id>/` |
| Models / Classification | `openpvscope.ml` | `models/`, `classification/` |
| Outputs | `openpvscope.exports` | `exports/` |

## Map-centric detection & segmentation

After orthophotos exist, **Detection** and **Segmentation** share one persistent MapLibre **PlantMap** (RGB + aligned thermal overlays, opacity dock, GeoJSON vectors). Tool chrome swaps with the step; the map instance is not remounted.

```
React PlantMap + DetectionTools / SegmentationTools
        │
        ▼
FastAPI  /api/detection/*  /api/segmentation/*  /api/map/*
        │
        ▼
Pure Python  detection.grid → template_match → pipeline
             segmentation.pairing → extract (windowed GeoTIFF crops)
```

- Detection: draw 4-corner AOI per modality (RGB / thermal) → rows×cols grid → **Copy RGB→Thermal** optional → deskew AOI window → OpenCV template match + NMS → **oriented** `panels.geojson` from seed cell shape (async job; run RGB | Thermal | Both). Edit outer 4 corners after generate regenerates the grid.
- Segmentation: pair **RGB** panels to the same geo rings on `thermal_aligned.tif`, windowed RGB/thermal crops + stats, `pairs.json` / `pairs.geojson`, on-demand PanelInspector. Thermal panels are for map QA (not required for pairing).
- Workflow: `mark_step(..., DONE)` only on first completion of each step (re-runs overwrite artifacts without cascading unlocks).
- History: checkpoints before AOI replace, grid, copy-to-thermal, detect, and segment.

## Project storage

A live project is a **user-chosen folder** containing a `.opsx` JSON descriptor and working data (`inputs/`, `alignment/`, …). Every meaningful change autosaves into that folder. Portable backups use **`.opsz`** (ZIP of the whole folder). See [opsx_format.md](opsx_format.md).
