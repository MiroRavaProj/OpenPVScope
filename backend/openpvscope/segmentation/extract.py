"""
Panel crop extraction — RGB ↔ thermal pairing port from legacy suite.

Previews: oriented (deskewed) crops with margin (default 0.2).
Thermal stats: exact panel rect, full resolution, no margin, values > -100,
includes variance.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.windows import Window

from openpvscope.detection.pipeline import load_geojson
from openpvscope.geo.crs import feature_collection, polygon_feature, transformer_to_wgs84
from openpvscope.io_atomic import atomic_write_json
from openpvscope.project.paths import ortho_rgb, ortho_thermal, ortho_thermal_aligned
from openpvscope.segmentation.pairing import (
    DEFAULT_MIN_IOU,
    pair_panels_self,
    pair_rgb_thermal_panels,
)

SEGMENTATION_REV = "seg-v3"
PREVIEW_MAX = 512
ProgressCb = Callable[[float | None, str], None]


def segmentation_root(project_root: Path) -> Path:
    p = Path(project_root) / "segmentation"
    p.mkdir(parents=True, exist_ok=True)
    (p / "panels").mkdir(parents=True, exist_ok=True)
    return p


def _lonlat_to_xy(lon: float, lat: float, from_wgs) -> tuple[float, float]:
    if from_wgs is None:
        return lon, lat
    x, y = from_wgs.transform(lon, lat)
    return float(x), float(y)


def _ring_pixel_pts(
    ring: list[list[float]],
    *,
    affine,
    from_wgs,
) -> np.ndarray:
    """Panel ring → Nx2 float32 pixel coords (col, row)."""
    pts: list[list[float]] = []
    for p in ring:
        if len(p) < 2:
            continue
        lon, lat = float(p[0]), float(p[1])
        x, y = _lonlat_to_xy(lon, lat, from_wgs)
        row, col = rasterio.transform.rowcol(affine, x, y)
        pts.append([float(col), float(row)])
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("Panel ring needs ≥3 vertices")
    return np.asarray(pts[:4] if len(pts) >= 4 else pts, dtype=np.float32)


def _order_box_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points TL, TR, BR, BL (OpenCV boxPoints → consistent warp)."""
    # Sum / diff heuristic
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.asarray([tl, tr, br, bl], dtype=np.float32)


def _thermal_stats(arr: np.ndarray) -> dict[str, float | None | str]:
    """Legacy: valid = values > -100 (nodata filter)."""
    valid = arr[np.isfinite(arr) & (arr > -100.0)]
    if valid.size == 0:
        return {
            "min_temperature": None,
            "max_temperature": None,
            "mean_temperature": None,
            "median_temperature": None,
            "std_temperature": None,
            "var_temperature": None,
            "temperature_unit": "Celsius",
            "valid_pixels": 0,
        }
    return {
        "min_temperature": float(valid.min()),
        "max_temperature": float(valid.max()),
        "mean_temperature": float(valid.mean()),
        "median_temperature": float(np.median(valid)),
        "std_temperature": float(valid.std()),
        "var_temperature": float(valid.var()),
        "temperature_unit": "Celsius",
        "valid_pixels": int(valid.size),
    }


def _stretch_u8(arr: np.ndarray) -> np.ndarray:
    """HxW float/int → uint8; CxHxW → HxWx3 uint8."""
    a = arr.astype(np.float32)
    if a.ndim == 2:
        valid = np.isfinite(a) & (a > -100.0 if a.dtype == np.float32 else True)
        # For RGB byte data, use isfinite only
        if not np.any(np.isfinite(a)):
            return np.zeros(a.shape, dtype=np.uint8)
        # Prefer > -100 for thermal-like floats; for RGB use percentiles of all finite
        mask = np.isfinite(a)
        if a.max() > 200 or a.min() < -50:
            mask = mask & (a > -100.0)
        if not np.any(mask):
            mask = np.isfinite(a)
        if not np.any(mask):
            return np.zeros(a.shape, dtype=np.uint8)
        lo, hi = np.percentile(a[mask], [2, 98])
        if hi <= lo:
            hi = lo + 1.0
        out = np.zeros(a.shape, dtype=np.uint8)
        out[mask] = np.clip((a[mask] - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
        return out
    # CxHxW
    bands = [_stretch_u8(a[i]) for i in range(min(3, a.shape[0]))]
    while len(bands) < 3:
        bands.append(bands[0])
    return np.stack(bands, axis=-1)


def _downscale_u8(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max(h / max_side, w / max_side, 1.0)
    if scale <= 1.0:
        return img
    nw, nh = max(1, int(round(w / scale))), max(1, int(round(h / scale)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


def crop_oriented_panel(
    ds,
    ring_lonlat: list[list[float]],
    from_wgs,
    *,
    margin_factor: float = 0.0,
    is_rgb: bool = False,
) -> np.ndarray:
    """
    Deskewed panel crop via perspective warp of a local window.

    Output size follows ordered box edge lengths (TL→TR, TL→BL), not OpenCV
    minAreaRect (w,h) which often swaps axes. Longer side is forced horizontal.
    """
    pts = _ring_pixel_pts(ring_lonlat, affine=ds.transform, from_wgs=from_wgs)
    rect = cv2.minAreaRect(pts)
    box = _order_box_points(cv2.boxPoints(rect))  # TL, TR, BR, BL
    top = float(np.linalg.norm(box[1] - box[0]))
    left = float(np.linalg.norm(box[3] - box[0]))
    top = max(top, 1.0)
    left = max(left, 1.0)

    # Landscape: longer side along X (matches deskewed templates)
    if top < left:
        box = np.asarray([box[3], box[0], box[1], box[2]], dtype=np.float32)
        top, left = left, top

    scale = 1.0 + 2.0 * float(margin_factor)
    out_w = max(1, int(round(top * scale)))
    out_h = max(1, int(round(left * scale)))

    center = box.mean(axis=0)
    src_world = center + (box - center) * scale

    min_c = int(np.floor(src_world[:, 0].min())) - 2
    max_c = int(np.ceil(src_world[:, 0].max())) + 2
    min_r = int(np.floor(src_world[:, 1].min())) - 2
    max_r = int(np.ceil(src_world[:, 1].max())) + 2
    min_c = max(0, min_c)
    min_r = max(0, min_r)
    max_c = min(ds.width, max_c)
    max_r = min(ds.height, max_r)
    if max_c <= min_c or max_r <= min_r:
        raise ValueError("Panel window outside raster")

    win_w = max_c - min_c
    win_h = max_r - min_r
    window = Window(min_c, min_r, win_w, win_h)
    src_local = src_world.copy()
    src_local[:, 0] -= min_c
    src_local[:, 1] -= min_r
    dst = np.asarray(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(src_local.astype(np.float32), dst)

    if is_rgb:
        if ds.count >= 3:
            data = ds.read([1, 2, 3], window=window, boundless=True, fill_value=0)
            patch = np.transpose(data, (1, 2, 0)).astype(np.float32)
        else:
            g = ds.read(1, window=window, boundless=True, fill_value=0).astype(np.float32)
            patch = np.stack([g, g, g], axis=-1)
        warped = cv2.warpPerspective(
            patch,
            M,
            (out_w, out_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        return _stretch_u8(np.transpose(warped, (2, 0, 1)))

    raw = ds.read(
        1,
        window=window,
        boundless=True,
        fill_value=np.nan,
    ).astype(np.float32)
    warped = cv2.warpPerspective(
        raw,
        M,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=np.nan,
    )
    return warped.astype(np.float32)


def run_segmentation(
    root: Path,
    *,
    margin_factor: float = 0.2,
    search_radius_m: float | None = None,
    min_iou: float = DEFAULT_MIN_IOU,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    root = Path(root)
    from openpvscope.photogrammetry.setup import load_setup

    thermal_only = load_setup(root).get("modalities") == "thermal_only"
    mode = "thermal_only" if thermal_only else "pair"

    th_panels = load_geojson(root, "panels", modality="thermal")
    if not th_panels or not th_panels.get("features"):
        raise FileNotFoundError("No thermal panels — run thermal detection first")

    th_path = ortho_thermal_aligned(root)
    if not th_path.is_file():
        th_path = ortho_thermal(root)
    if not th_path.is_file():
        raise FileNotFoundError("Thermal orthophoto missing")

    def prog(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    if thermal_only:
        prog(5, f"Preparing thermal panels [{SEGMENTATION_REV}]")
        pairs = pair_panels_self(th_panels)
        if not pairs:
            raise RuntimeError("No thermal panels to extract")
    else:
        rgb_panels = load_geojson(root, "panels", modality="rgb")
        if not rgb_panels or not rgb_panels.get("features"):
            raise FileNotFoundError("No RGB panels — run RGB detection first")

        rgb_path = ortho_rgb(root)
        if not rgb_path.is_file():
            raise FileNotFoundError("RGB orthophoto missing")

        prog(5, f"Pairing RGB↔thermal panels [{SEGMENTATION_REV}]")
        pairs = pair_rgb_thermal_panels(
            rgb_panels,
            th_panels,
            search_radius_m=search_radius_m,
            min_iou=min_iou,
        )
        if not pairs:
            raise RuntimeError(
                f"No RGB↔thermal pairs (min IoU={min_iou}, "
                f"search_radius_m={search_radius_m or 'auto'}) — check both detections overlap"
            )

    seg = segmentation_root(root)
    panels_dir = seg / "panels"
    if panels_dir.is_dir():
        for child in panels_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    extract_label = "thermal" if thermal_only else "paired"
    prog(12, f"Extracting {len(pairs)} {extract_label} crops (deskewed, full-res stats)")
    out_pairs: list[dict] = []
    pair_features = []

    if thermal_only:
        with rasterio.open(th_path) as th_ds:
            from_wgs_th = None
            if transformer_to_wgs84(th_ds.crs) is not None:
                from_wgs_th = Transformer.from_crs("EPSG:4326", th_ds.crs, always_xy=True)

            n = max(1, len(pairs))
            for i, pair in enumerate(pairs):
                pid = pair["id"]
                th_ring = pair["thermal_ring"]

                th_preview_raw = crop_oriented_panel(
                    th_ds, th_ring, from_wgs_th, margin_factor=margin_factor, is_rgb=False
                )
                th_exact = crop_oriented_panel(
                    th_ds, th_ring, from_wgs_th, margin_factor=0.0, is_rgb=False
                )
                stats = _thermal_stats(th_exact)

                th_prev_u8 = _downscale_u8(_stretch_u8(th_preview_raw), PREVIEW_MAX)
                if th_prev_u8.ndim == 2:
                    th_prev_u8 = cv2.cvtColor(th_prev_u8, cv2.COLOR_GRAY2RGB)

                dest = panels_dir / pid
                dest.mkdir(parents=True, exist_ok=True)
                Image.fromarray(th_prev_u8).save(dest / "thermal.png")
                with rasterio.open(
                    dest / "thermal.tif",
                    "w",
                    driver="GTiff",
                    height=th_exact.shape[0],
                    width=th_exact.shape[1],
                    count=1,
                    dtype="float32",
                    compress="lzw",
                ) as dst:
                    dst.write(th_exact, 1)
                    dst.update_tags(
                        min_temperature=stats.get("min_temperature"),
                        max_temperature=stats.get("max_temperature"),
                        mean_temperature=stats.get("mean_temperature"),
                        median_temperature=stats.get("median_temperature"),
                        std_temperature=stats.get("std_temperature"),
                        var_temperature=stats.get("var_temperature"),
                        temperature_unit=stats.get("temperature_unit", "Celsius"),
                    )

                meta = {
                    "id": pid,
                    "thermal_id": pair.get("thermal_id"),
                    "confidence": pair.get("confidence"),
                    "margin_factor": margin_factor,
                    "segmentation_rev": SEGMENTATION_REV,
                    "mode": "thermal_only",
                    **stats,
                }
                atomic_write_json(dest / "meta.json", meta)

                ring = pair.get("ring") or th_ring
                out_pairs.append(
                    {
                        **pair,
                        "stats": stats,
                        "paths": {
                            "thermal": f"panels/{pid}/thermal.png",
                        },
                    }
                )
                pair_features.append(
                    polygon_feature(
                        [[float(p[0]), float(p[1])] for p in ring[:4]],
                        {
                            "kind": "thermal",
                            "id": pid,
                            "thermal_id": pair.get("thermal_id"),
                            "min_temperature": stats.get("min_temperature"),
                            "max_temperature": stats.get("max_temperature"),
                            "mean_temperature": stats.get("mean_temperature"),
                            "median_temperature": stats.get("median_temperature"),
                            "std_temperature": stats.get("std_temperature"),
                            "var_temperature": stats.get("var_temperature"),
                            "valid_pixels": stats.get("valid_pixels"),
                            "confidence": pair.get("confidence"),
                        },
                        fid=pid,
                    )
                )
                if i % 5 == 0 or i == n - 1:
                    prog(12 + 85 * (i + 1) / n, f"Cropped {i + 1}/{n} [{SEGMENTATION_REV}]")
    else:
        rgb_path = ortho_rgb(root)
        with rasterio.open(rgb_path) as rgb_ds, rasterio.open(th_path) as th_ds:
            from_wgs_rgb = None
            if transformer_to_wgs84(rgb_ds.crs) is not None:
                from_wgs_rgb = Transformer.from_crs("EPSG:4326", rgb_ds.crs, always_xy=True)
            from_wgs_th = None
            if transformer_to_wgs84(th_ds.crs) is not None:
                from_wgs_th = Transformer.from_crs("EPSG:4326", th_ds.crs, always_xy=True)
            else:
                from_wgs_th = from_wgs_rgb

            n = max(1, len(pairs))
            for i, pair in enumerate(pairs):
                pid = pair["id"]
                rgb_ring = pair["rgb_ring"]
                th_ring = pair["thermal_ring"]

                rgb_preview = crop_oriented_panel(
                    rgb_ds, rgb_ring, from_wgs_rgb, margin_factor=margin_factor, is_rgb=True
                )
                th_preview_raw = crop_oriented_panel(
                    th_ds, th_ring, from_wgs_th, margin_factor=margin_factor, is_rgb=False
                )
                th_exact = crop_oriented_panel(
                    th_ds, th_ring, from_wgs_th, margin_factor=0.0, is_rgb=False
                )
                stats = _thermal_stats(th_exact)

                rgb_prev_u8 = _downscale_u8(rgb_preview, PREVIEW_MAX)
                th_prev_u8 = _downscale_u8(_stretch_u8(th_preview_raw), PREVIEW_MAX)
                if th_prev_u8.ndim == 2:
                    th_prev_u8 = cv2.cvtColor(th_prev_u8, cv2.COLOR_GRAY2RGB)

                dest = panels_dir / pid
                dest.mkdir(parents=True, exist_ok=True)
                Image.fromarray(rgb_prev_u8).save(dest / "rgb.png")
                Image.fromarray(th_prev_u8).save(dest / "thermal.png")
                with rasterio.open(
                    dest / "thermal.tif",
                    "w",
                    driver="GTiff",
                    height=th_exact.shape[0],
                    width=th_exact.shape[1],
                    count=1,
                    dtype="float32",
                    compress="lzw",
                ) as dst:
                    dst.write(th_exact, 1)
                    dst.update_tags(
                        min_temperature=stats.get("min_temperature"),
                        max_temperature=stats.get("max_temperature"),
                        mean_temperature=stats.get("mean_temperature"),
                        median_temperature=stats.get("median_temperature"),
                        std_temperature=stats.get("std_temperature"),
                        var_temperature=stats.get("var_temperature"),
                        temperature_unit=stats.get("temperature_unit", "Celsius"),
                    )

                meta = {
                    "id": pid,
                    "rgb_id": pair.get("rgb_id"),
                    "thermal_id": pair.get("thermal_id"),
                    "confidence": pair.get("confidence"),
                    "thermal_confidence": pair.get("thermal_confidence"),
                    "iou": pair.get("iou"),
                    "distance_m": pair.get("distance_m"),
                    "margin_factor": margin_factor,
                    "min_iou": min_iou,
                    "segmentation_rev": SEGMENTATION_REV,
                    **stats,
                }
                atomic_write_json(dest / "meta.json", meta)

                ring = pair.get("ring") or rgb_ring
                out_pairs.append(
                    {
                        **pair,
                        "stats": stats,
                        "paths": {
                            "rgb": f"panels/{pid}/rgb.png",
                            "thermal": f"panels/{pid}/thermal.png",
                        },
                    }
                )
                pair_features.append(
                    polygon_feature(
                        [[float(p[0]), float(p[1])] for p in ring[:4]],
                        {
                            "kind": "pair",
                            "id": pid,
                            "rgb_id": pair.get("rgb_id"),
                            "thermal_id": pair.get("thermal_id"),
                            "min_temperature": stats.get("min_temperature"),
                            "max_temperature": stats.get("max_temperature"),
                            "mean_temperature": stats.get("mean_temperature"),
                            "median_temperature": stats.get("median_temperature"),
                            "std_temperature": stats.get("std_temperature"),
                            "var_temperature": stats.get("var_temperature"),
                            "valid_pixels": stats.get("valid_pixels"),
                            "confidence": pair.get("confidence"),
                            "thermal_confidence": pair.get("thermal_confidence"),
                            "iou": pair.get("iou"),
                            "distance_m": pair.get("distance_m"),
                        },
                        fid=pid,
                    )
                )
                if i % 5 == 0 or i == n - 1:
                    prog(12 + 85 * (i + 1) / n, f"Cropped {i + 1}/{n} [{SEGMENTATION_REV}]")

    done_label = "thermal panels" if thermal_only else "pairs"
    atomic_write_json(
        seg / "pairs.json",
        {
            "pairs": out_pairs,
            "count": len(out_pairs),
            "mode": mode,
            "margin_factor": margin_factor,
            "min_iou": min_iou,
            "search_radius_m": search_radius_m,
            "segmentation_rev": SEGMENTATION_REV,
        },
    )
    atomic_write_json(seg / "pairs.geojson", feature_collection(pair_features, name="pairs"))
    prog(100, f"Segmentation complete — {len(out_pairs)} {done_label} [{SEGMENTATION_REV}]")
    return {
        "count": len(out_pairs),
        "mode": mode,
        "segmentation_rev": SEGMENTATION_REV,
        "margin_factor": margin_factor,
        "min_iou": min_iou,
    }


def segmentation_status(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    pairs_path = segmentation_root(root) / "pairs.json"
    count = 0
    rev = None
    mode = None
    if pairs_path.is_file():
        try:
            data = json.loads(pairs_path.read_text(encoding="utf-8"))
            count = int(data.get("count") or 0)
            rev = data.get("segmentation_rev")
            mode = data.get("mode")
        except Exception:
            count = 0
    thermal_only = False
    try:
        from openpvscope.photogrammetry.setup import load_setup

        thermal_only = load_setup(root).get("modalities") == "thermal_only"
    except Exception:
        thermal_only = mode == "thermal_only"
    if thermal_only or mode == "thermal_only":
        msg = "Run thermal segmentation after detection" if not count else f"{count} thermal panels"
        if rev and count:
            msg = f"{count} thermal panels [{rev}]"
    else:
        msg = f"{count} panel pairs" if count else "Run segmentation after RGB + thermal detection"
        if rev and count:
            msg = f"{count} panel pairs [{rev}]"
    return {
        "ready": count > 0,
        "message": msg,
        "has_pairs": count > 0,
        "pair_count": count,
        "segmentation_rev": rev,
        "mode": mode or ("thermal_only" if thermal_only else "pair"),
    }


def load_pairs_geojson_enriched(project_root: Path) -> dict[str, Any]:
    """
    Load pairs.geojson and backfill thermal stats from pairs.json / meta.json
    when older extracts only stored mean/var on the GeoJSON.
    """
    root = Path(project_root)
    seg = segmentation_root(root)
    gj_path = seg / "pairs.geojson"
    if not gj_path.is_file():
        return {"type": "FeatureCollection", "features": []}
    fc = json.loads(gj_path.read_text(encoding="utf-8"))
    by_id: dict[str, dict] = {}
    pairs_path = seg / "pairs.json"
    if pairs_path.is_file():
        try:
            for p in json.loads(pairs_path.read_text(encoding="utf-8")).get("pairs") or []:
                pid = str(p.get("id") or "")
                if not pid:
                    continue
                stats = p.get("stats") or {}
                by_id[pid] = {
                    "rgb_id": p.get("rgb_id"),
                    "thermal_id": p.get("thermal_id"),
                    "iou": p.get("iou"),
                    "distance_m": p.get("distance_m"),
                    "confidence": p.get("confidence"),
                    "thermal_confidence": p.get("thermal_confidence"),
                    **{k: stats.get(k) for k in (
                        "min_temperature",
                        "max_temperature",
                        "mean_temperature",
                        "median_temperature",
                        "std_temperature",
                        "var_temperature",
                        "valid_pixels",
                    )},
                }
        except Exception:
            by_id = {}

    for feat in fc.get("features") or []:
        props = feat.setdefault("properties", {})
        pid = str(props.get("id") or feat.get("id") or "")
        src = by_id.get(pid)
        if not src:
            meta_path = seg / "panels" / pid / "meta.json"
            if meta_path.is_file():
                try:
                    src = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    src = None
        if not src:
            continue
        for k, v in src.items():
            if props.get(k) is None and v is not None:
                props[k] = v
    return fc


def read_thermal_raw(project_root: Path, panel_id: str) -> dict[str, Any]:
    """Float32 thermal crop as flat list for interactive viewer."""
    import math

    safe = "".join(c for c in panel_id if c.isalnum() or c in "-_")
    path = segmentation_root(project_root) / "panels" / safe / "thermal.tif"
    if not path.is_file():
        raise FileNotFoundError("thermal.tif not found")
    with rasterio.open(path) as ds:
        arr = ds.read(1).astype(np.float32)
    h, w = int(arr.shape[0]), int(arr.shape[1])
    flat = arr.reshape(-1)
    data: list[float | None] = []
    for v in flat:
        fv = float(v)
        if not math.isfinite(fv):
            data.append(None)
        else:
            data.append(fv)
    valid = [v for v in data if v is not None and v > -100]
    return {
        "width": w,
        "height": h,
        "data": data,
        "min": min(valid) if valid else None,
        "max": max(valid) if valid else None,
        "mean": float(sum(valid) / len(valid)) if valid else None,
    }
