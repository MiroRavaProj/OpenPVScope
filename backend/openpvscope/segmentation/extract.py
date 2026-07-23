"""Windowed panel crop extraction from RGB + thermal_aligned GeoTIFFs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from openpvscope.detection.pipeline import load_geojson
from openpvscope.geo.crs import feature_collection, polygon_feature, transformer_to_wgs84
from openpvscope.io_atomic import atomic_write_json
from openpvscope.project.paths import ortho_rgb, ortho_thermal_aligned
from openpvscope.segmentation.pairing import pair_panels_self

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


def _stretch_preview(arr: np.ndarray) -> np.ndarray:
    """Single band or RGB → HxW uint8 or HxWx3 uint8."""
    a = arr.astype(np.float32)
    if a.ndim == 2:
        valid = np.isfinite(a)
        if not np.any(valid):
            return np.zeros_like(a, dtype=np.uint8)
        lo, hi = np.percentile(a[valid], [2, 98])
        if hi <= lo:
            hi = lo + 1
        return np.clip((a - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
    # CxHxW
    bands = []
    for i in range(min(3, a.shape[0])):
        bands.append(_stretch_preview(a[i]))
    while len(bands) < 3:
        bands.append(bands[0])
    return np.stack(bands, axis=-1)


def _thermal_stats(arr: np.ndarray) -> dict[str, float | None]:
    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        return {
            "min_temperature": None,
            "max_temperature": None,
            "mean_temperature": None,
            "median_temperature": None,
            "std_temperature": None,
        }
    return {
        "min_temperature": float(valid.min()),
        "max_temperature": float(valid.max()),
        "mean_temperature": float(valid.mean()),
        "median_temperature": float(np.median(valid)),
        "std_temperature": float(valid.std()),
        "temperature_unit": "Celsius",
    }


def _read_window_rgb(ds, west, south, east, north, out_max: int = 256) -> np.ndarray:
    window = from_bounds(west, south, east, north, transform=ds.transform)
    scale = max(window.width / out_max, window.height / out_max, 1.0)
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
    return _stretch_preview(data)


def _read_window_thermal(ds, west, south, east, north, out_max: int = 256) -> tuple[np.ndarray, np.ndarray]:
    window = from_bounds(west, south, east, north, transform=ds.transform)
    scale = max(window.width / out_max, window.height / out_max, 1.0)
    out_w = max(1, int(window.width / scale))
    out_h = max(1, int(window.height / scale))
    raw = ds.read(
        1,
        window=window,
        out_shape=(out_h, out_w),
        resampling=Resampling.bilinear,
        boundless=True,
        fill_value=np.nan,
    ).astype(np.float32)
    preview = _stretch_preview(raw)
    return raw, preview


def run_segmentation(
    root: Path,
    *,
    margin_factor: float = 0.15,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    root = Path(root)
    panels = load_geojson(root, "panels")
    if not panels or not panels.get("features"):
        raise FileNotFoundError("No detected panels — run detection first")
    rgb_path = ortho_rgb(root)
    th_path = ortho_thermal_aligned(root)
    if not rgb_path.is_file():
        raise FileNotFoundError("RGB orthophoto missing")
    if not th_path.is_file():
        raise FileNotFoundError("Aligned thermal orthophoto missing — complete ortho alignment first")

    def prog(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    prog(5, "Pairing panels")
    pairs = pair_panels_self(panels)
    seg = segmentation_root(root)
    # clear old panel dirs
    panels_dir = seg / "panels"
    if panels_dir.is_dir():
        for child in panels_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    from pyproj import Transformer

    prog(15, f"Extracting {len(pairs)} panel crops")
    out_pairs: list[dict] = []
    pair_features = []

    with rasterio.open(rgb_path) as rgb_ds, rasterio.open(th_path) as th_ds:
        from_wgs_rgb = None
        to_wgs = transformer_to_wgs84(rgb_ds.crs)
        if to_wgs is not None:
            from_wgs_rgb = Transformer.from_crs("EPSG:4326", rgb_ds.crs, always_xy=True)
        from_wgs_th = None
        if transformer_to_wgs84(th_ds.crs) is not None:
            from_wgs_th = Transformer.from_crs("EPSG:4326", th_ds.crs, always_xy=True)
        else:
            from_wgs_th = from_wgs_rgb

        n = max(1, len(pairs))
        for i, pair in enumerate(pairs):
            pid = pair["id"]
            ring = pair["ring"]
            xs_ll = [p[0] for p in ring]
            ys_ll = [p[1] for p in ring]
            # margin in lon/lat fraction of bbox
            minx, maxx = min(xs_ll), max(xs_ll)
            miny, maxy = min(ys_ll), max(ys_ll)
            dx = (maxx - minx) * margin_factor + 1e-9
            dy = (maxy - miny) * margin_factor + 1e-9
            minx -= dx
            maxx += dx
            miny -= dy
            maxy += dy

            # RGB window in RGB CRS
            corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
            rgb_xy = [_lonlat_to_xy(x, y, from_wgs_rgb) for x, y in corners]
            th_xy = [_lonlat_to_xy(x, y, from_wgs_th) for x, y in corners]
            rw = min(p[0] for p in rgb_xy), min(p[1] for p in rgb_xy), max(p[0] for p in rgb_xy), max(p[1] for p in rgb_xy)
            tw = min(p[0] for p in th_xy), min(p[1] for p in th_xy), max(p[0] for p in th_xy), max(p[1] for p in th_xy)

            rgb_img = _read_window_rgb(rgb_ds, rw[0], rw[1], rw[2], rw[3])
            th_raw, th_prev = _read_window_thermal(th_ds, tw[0], tw[1], tw[2], tw[3])
            stats = _thermal_stats(th_raw)

            dest = panels_dir / pid
            dest.mkdir(parents=True, exist_ok=True)
            Image.fromarray(rgb_img).save(dest / "rgb.png")
            Image.fromarray(th_prev).save(dest / "thermal.png")
            # raw thermal small geotiff-less npy alternative: save float32 tif without geo
            with rasterio.open(
                dest / "thermal.tif",
                "w",
                driver="GTiff",
                height=th_raw.shape[0],
                width=th_raw.shape[1],
                count=1,
                dtype="float32",
                compress="lzw",
            ) as dst:
                dst.write(th_raw, 1)

            meta = {
                "id": pid,
                "confidence": pair.get("confidence"),
                "iou": pair.get("iou"),
                "margin_factor": margin_factor,
                **stats,
            }
            atomic_write_json(dest / "meta.json", meta)
            out_pairs.append({**pair, "stats": stats, "paths": {"rgb": f"panels/{pid}/rgb.png", "thermal": f"panels/{pid}/thermal.png"}})
            pair_features.append(
                polygon_feature(
                    [[p[0], p[1]] for p in ring[:4]],
                    {
                        "kind": "pair",
                        "id": pid,
                        "mean_temperature": stats.get("mean_temperature"),
                        "confidence": pair.get("confidence"),
                    },
                    fid=pid,
                )
            )
            if i % 10 == 0 or i == n - 1:
                prog(15 + 80 * (i + 1) / n, f"Cropped {i + 1}/{n}")

    atomic_write_json(seg / "pairs.json", {"pairs": out_pairs, "count": len(out_pairs)})
    atomic_write_json(seg / "pairs.geojson", feature_collection(pair_features, name="pairs"))
    prog(100, f"Segmentation complete — {len(out_pairs)} pairs")
    return {"count": len(out_pairs)}


def segmentation_status(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    pairs_path = segmentation_root(root) / "pairs.json"
    count = 0
    if pairs_path.is_file():
        try:
            count = int(json.loads(pairs_path.read_text(encoding="utf-8")).get("count") or 0)
        except Exception:
            count = 0
    return {
        "ready": count > 0,
        "message": f"{count} panel pairs" if count else "Run segmentation after detection",
        "has_pairs": count > 0,
        "pair_count": count,
    }
