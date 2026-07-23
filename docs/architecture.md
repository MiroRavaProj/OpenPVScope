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
│  │  project / ingest / opensfm / align…  │  │
│  └──────────────────┬────────────────────┘  │
│                     │                       │
│  ┌──────────────────▼────────────────────┐  │
│  │  Pure Python core (no UI imports)     │  │
│  │  .opsx pack/unpack · alignment · …    │  │
│  └──────────────────┬────────────────────┘  │
│                     │ subprocess            │
│  ┌──────────────────▼────────────────────┐  │
│  │  OpenSfM 1.0 (bundled conda env)      │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

## Design principles

- **Guided pipeline** — new projects start at Photogrammetry; skip to GeoTIFF import is explicit.
- **UI is a client** — business logic lives in `openpvscope/` with no React/NiceGUI imports.
- **`.opsx` projects** — staged zip package; never one mega-JSON of all panels.
- **Self-contained Windows install** — no Docker for end users.

## Pipeline stages

| Step | Module | Writes into `.opsx` |
|------|--------|---------------------|
| Photogrammetry | `openpvscope.opensfm` | `inputs/ortho/`, `photogrammetry/` |
| Alignment | `openpvscope.alignment` | `alignment/`, `thermal_aligned.tif` |
| Detection | `openpvscope.detection` | `detection/` |
| Segmentation | `openpvscope.segmentation` | `segmentation/` |
| Models / Classification | `openpvscope.ml` | `models/`, `classification/` |
| Outputs | `openpvscope.exports` | `exports/` |

## Working cache

Opening a `.opsx` unpacks to a cache directory. Saving repacks the cache into `.opsx` (large GeoTIFFs stored uncompressed in the zip).
