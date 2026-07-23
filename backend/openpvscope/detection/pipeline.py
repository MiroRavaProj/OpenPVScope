"""Detection pipeline: AOI/grid GeoJSON + template matching → panels.geojson."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from openpvscope.detection.grid import build_grid_cells, regularize_quad
from openpvscope.detection.template_match import extract_patch_rgb, match_template_multichannel
from openpvscope.geo.crs import (
    feature_collection,
    polygon_feature,
    ring_to_lonlat,
    transformer_to_wgs84,
)
from openpvscope.io_atomic import atomic_write_json
from openpvscope.project.paths import ortho_rgb

ProgressCb = Callable[[float | None, str], None]


def detection_dir(root: Path) -> Path:
    d = Path(root) / "detection" / "rgb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_aoi_geojson(root: Path, ring_lonlat: list[list[float]]) -> Path:
    """ring_lonlat: [[lon,lat], ...] exactly 4 corners (open or closed)."""
    pts = [(float(p[0]), float(p[1])) for p in ring_lonlat[:4]]
    if len(pts) != 4:
        raise ValueError("AOI needs 4 corners")
    rect = regularize_quad(pts)
    tr = None  # already lon/lat from map
    feat = polygon_feature(
        [[x, y] for x, y in rect],
        {"kind": "aoi"},
        fid="aoi",
    )
    # also store CRS-space copy? Map is WGS84; matching needs projected/native.
    # We store WGS84 in geojson and reproject to raster CRS when running.
    out = detection_dir(root) / "aoi.geojson"
    atomic_write_json(out, feature_collection([feat], name="aoi"))
    # sidecar with open ring for grid
    atomic_write_json(
        detection_dir(root) / "aoi_ring.json",
        {"ring": [[x, y] for x, y in rect], "crs": "EPSG:4326"},
    )
    return out


def generate_grid(
    root: Path,
    *,
    rows: int,
    cols: int,
) -> dict[str, Any]:
    ring_path = detection_dir(root) / "aoi_ring.json"
    data = _read_json(ring_path)
    if not data or not data.get("ring"):
        raise FileNotFoundError("Save an AOI before generating the grid")
    ring = [tuple(p) for p in data["ring"]]
    cells = build_grid_cells(ring, rows, cols)
    features = []
    for cell in cells:
        fid = f"g-{cell['row']}-{cell['col']}"
        features.append(
            polygon_feature(
                [[x, y] for x, y in cell["ring"]],
                {"kind": "grid", "row": cell["row"], "col": cell["col"]},
                fid=fid,
            )
        )
    fc = feature_collection(features, name="grid")
    out = detection_dir(root) / "grid.geojson"
    atomic_write_json(out, fc)
    atomic_write_json(
        detection_dir(root) / "grid_meta.json",
        {"rows": rows, "cols": cols, "cell_count": len(cells)},
    )
    return {"rows": rows, "cols": cols, "cell_count": len(cells), "path": str(out)}


def _lonlat_to_xy(lon: float, lat: float, transformer_from_wgs84) -> tuple[float, float]:
    if transformer_from_wgs84 is None:
        return lon, lat
    x, y = transformer_from_wgs84.transform(lon, lat)
    return float(x), float(y)


def _stretch_rgb_uint8(arr: np.ndarray) -> np.ndarray:
    """arr CxHxW or HxWxC float/int → HxWx3 uint8."""
    if arr.ndim == 3 and arr.shape[0] in (1, 3, 4) and arr.shape[0] < arr.shape[-1]:
        # CxHxW
        if arr.shape[0] >= 3:
            rgb = np.transpose(arr[:3], (1, 2, 0))
        else:
            g = arr[0]
            rgb = np.stack([g, g, g], axis=-1)
    else:
        rgb = arr[:, :, :3] if arr.ndim == 3 else np.stack([arr, arr, arr], axis=-1)

    rgb = rgb.astype(np.float32)
    out = np.zeros_like(rgb, dtype=np.uint8)
    for i in range(3):
        band = rgb[:, :, i]
        valid = np.isfinite(band)
        if not np.any(valid):
            continue
        lo, hi = np.percentile(band[valid], [2, 98])
        if hi <= lo:
            hi = lo + 1
        scaled = np.clip((band - lo) / (hi - lo) * 255.0, 0, 255)
        out[:, :, i] = scaled.astype(np.uint8)
    return out


def run_detection(
    root: Path,
    *,
    confidence: float = 0.55,
    nms_iou: float = 0.15,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    root = Path(root)
    rgb_path = ortho_rgb(root)
    if not rgb_path.is_file():
        raise FileNotFoundError("RGB orthophoto required")

    def prog(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    prog(5, "Loading AOI and grid")
    aoi = _read_json(detection_dir(root) / "aoi_ring.json")
    grid_fc = _read_json(detection_dir(root) / "grid.geojson")
    if not aoi or not grid_fc:
        raise FileNotFoundError("AOI and grid required — generate grid first")

    ring_wgs = aoi["ring"]
    prog(15, "Reading RGB window")

    with rasterio.open(rgb_path) as ds:
        from pyproj import Transformer

        to_wgs = transformer_to_wgs84(ds.crs)
        from_wgs = None
        if to_wgs is not None:
            from_wgs = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)

        xs, ys = [], []
        for lon, lat in ring_wgs:
            x, y = _lonlat_to_xy(float(lon), float(lat), from_wgs)
            xs.append(x)
            ys.append(y)
        west, east = min(xs), max(xs)
        south, north = min(ys), max(ys)
        # pad slightly
        pad_x = (east - west) * 0.02 + abs(ds.transform.a)
        pad_y = (north - south) * 0.02 + abs(ds.transform.e)
        west -= pad_x
        east += pad_x
        south -= pad_y
        north += pad_y

        window = from_bounds(west, south, east, north, transform=ds.transform)
        # Read at native or downscaled if huge
        max_dim = 4000
        scale = max(window.width / max_dim, window.height / max_dim, 1.0)
        out_w = max(1, int(window.width / scale))
        out_h = max(1, int(window.height / scale))
        if ds.count >= 3:
            data = ds.read(
                [1, 2, 3],
                window=window,
                out_shape=(3, out_h, out_w),
                resampling=Resampling.bilinear,
                boundless=True,
                fill_value=0,
            )
        else:
            g = ds.read(
                1,
                window=window,
                out_shape=(out_h, out_w),
                resampling=Resampling.bilinear,
                boundless=True,
                fill_value=0,
            )
            data = np.stack([g, g, g], axis=0)

        image = _stretch_rgb_uint8(data)
        # Affine from window pixel → CRS
        from rasterio.windows import transform as window_transform
        from rasterio.transform import Affine

        win_transform = window_transform(window, ds.transform)
        # Scale for out resolution
        pix_transform = win_transform * Affine.scale(window.width / out_w, window.height / out_h)

        def crs_to_pixel(x: float, y: float) -> tuple[int, int]:
            inv = ~pix_transform
            c, r = inv * (x, y)
            return int(round(c)), int(round(r))

        def pixel_to_crs(c: float, r: float) -> tuple[float, float]:
            return pix_transform * (c, r)

        prog(35, "Building template from grid cells")
        templates: list[np.ndarray] = []
        for feat in grid_fc.get("features") or []:
            coords = feat["geometry"]["coordinates"][0]
            ring_xy = [_lonlat_to_xy(float(p[0]), float(p[1]), from_wgs) for p in coords[:4]]
            cols = [crs_to_pixel(x, y)[0] for x, y in ring_xy]
            rows = [crs_to_pixel(x, y)[1] for x, y in ring_xy]
            patch = extract_patch_rgb(image, min(cols), min(rows), max(cols), max(rows))
            if patch is not None and patch.shape[0] >= 4 and patch.shape[1] >= 4:
                templates.append(patch)
            if len(templates) >= 3:
                break
        if not templates:
            # fallback: use first grid cell bounds even if tiny
            raise RuntimeError("Could not extract a template from the grid — check AOI coverage on RGB")

        prog(50, "Running template matching")
        all_dets: list[dict] = []
        for ti, tpl in enumerate(templates):
            dets = match_template_multichannel(
                image, tpl, threshold=confidence, nms_iou=nms_iou
            )
            for d in dets:
                d["template_index"] = ti
            all_dets.extend(dets)

        # Global NMS across templates
        if all_dets:
            boxes = [d["bbox"] for d in all_dets]
            scores = [d["confidence"] for d in all_dets]
            from openpvscope.detection.template_match import nms as nms_fn

            keep = nms_fn(boxes, scores, nms_iou)
            all_dets = [all_dets[i] for i in keep]

        prog(80, f"Writing {len(all_dets)} panels")
        features = []
        for d in all_dets:
            x, y, w, h = d["bbox"]
            corners_px = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            ring_crs = [pixel_to_crs(c, r) for c, r in corners_px]
            ring_ll = ring_to_lonlat(ring_crs, to_wgs)
            pid = uuid.uuid4().hex[:12]
            features.append(
                polygon_feature(
                    ring_ll,
                    {"kind": "panel", "confidence": d["confidence"], "id": pid},
                    fid=pid,
                )
            )

        fc = feature_collection(features, name="panels")
        out = detection_dir(root) / "panels.geojson"
        atomic_write_json(out, fc)
        atomic_write_json(
            detection_dir(root) / "detection_meta.json",
            {
                "count": len(features),
                "confidence": confidence,
                "nms_iou": nms_iou,
                "templates_used": len(templates),
            },
        )

    prog(100, f"Detection complete — {len(features)} panels")
    return {"count": len(features), "path": str(out)}


def detection_status(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    d = detection_dir(root)
    panels = d / "panels.geojson"
    aoi = d / "aoi.geojson"
    grid = d / "grid.geojson"
    count = 0
    if panels.is_file():
        try:
            count = len(json.loads(panels.read_text(encoding="utf-8")).get("features") or [])
        except Exception:
            count = 0
    ready = panels.is_file() and count > 0
    return {
        "ready": ready,
        "message": f"{count} panels detected" if ready else "Draw AOI, generate grid, then run detection",
        "has_aoi": aoi.is_file(),
        "has_grid": grid.is_file(),
        "has_rgb_panels": panels.is_file(),
        "has_thermal_panels": False,
        "panel_count": count,
    }


def load_geojson(root: Path, name: str) -> dict | None:
    path = detection_dir(root) / f"{name}.geojson"
    return _read_json(path)
