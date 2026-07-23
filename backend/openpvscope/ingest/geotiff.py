"""GeoTIFF ingest and map overlay helpers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _safe_open_rasterio(path: Path):
    try:
        import rasterio
    except ImportError as e:
        raise RuntimeError("rasterio is required for GeoTIFF support") from e
    return rasterio.open(path)


def inspect_geotiff(path: Path) -> dict[str, Any]:
    path = Path(path)
    with _safe_open_rasterio(path) as ds:
        crs = ds.crs.to_string() if ds.crs else None
        bounds = ds.bounds
        return {
            "path": str(path),
            "name": path.name,
            "width": ds.width,
            "height": ds.height,
            "count": ds.count,
            "dtype": str(ds.dtypes[0]),
            "crs": crs,
            "is_georeferenced": bool(ds.crs and ds.transform),
            "bounds": {
                "left": bounds.left,
                "bottom": bounds.bottom,
                "right": bounds.right,
                "top": bounds.top,
            },
            "transform": list(ds.transform)[:6],
        }


def _stretch_uint8(arr: np.ndarray, nodata=None) -> np.ndarray:
    a = arr.astype(np.float32)
    mask = np.isfinite(a)
    if nodata is not None and np.isfinite(nodata):
        mask &= a != nodata
    # Ignore extreme negative sentinels
    mask &= a > -1e4
    valid = a[mask]
    if valid.size == 0:
        return np.zeros(a.shape, dtype=np.uint8)
    lo, hi = np.percentile(valid, [2, 98])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(valid.min()), float(valid.max()) + 1e-6
    out = (np.clip(a, lo, hi) - lo) / (hi - lo) * 255.0
    out = np.where(mask, out, 0)
    return out.astype(np.uint8)


def create_preview_png(
    geotiff_path: Path,
    out_png: Path,
    max_dim: int = 2048,
) -> dict[str, Any]:
    """Write a display PNG and return bounds + size for MapLibre image source."""
    geotiff_path = Path(geotiff_path)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    with _safe_open_rasterio(geotiff_path) as ds:
        scale = max(ds.width, ds.height) / max_dim
        out_w = max(1, int(math.ceil(ds.width / max(scale, 1))))
        out_h = max(1, int(math.ceil(ds.height / max(scale, 1))))
        import rasterio
        from rasterio.enums import Resampling

        if ds.count >= 3:
            data = ds.read(
                [1, 2, 3],
                out_shape=(3, out_h, out_w),
                resampling=Resampling.bilinear,
            )
            if data.dtype == np.uint8:
                rgb = np.transpose(data, (1, 2, 0))
            else:
                bands = [_stretch_uint8(data[i], ds.nodata) for i in range(3)]
                rgb = np.stack(bands, axis=-1)
            img = Image.fromarray(rgb, mode="RGB")
        else:
            data = ds.read(
                1,
                out_shape=(out_h, out_w),
                resampling=Resampling.bilinear,
            )
            gray = _stretch_uint8(data, ds.nodata)
            # Thermal-ish colormap: grayscale for simplicity; UI can restyle
            img = Image.fromarray(gray, mode="L").convert("RGB")

        bounds = ds.bounds
        crs = ds.crs.to_string() if ds.crs else None

    img.save(out_png, format="PNG", optimize=True)
    return {
        "png": str(out_png),
        "width": img.width,
        "height": img.height,
        "crs": crs,
        "bounds": {
            "left": bounds.left,
            "bottom": bounds.bottom,
            "right": bounds.right,
            "top": bounds.top,
        },
    }
