"""GeoTIFF ingest and map overlay helpers."""

from __future__ import annotations

import io
import math
from functools import lru_cache
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


# Thermal / float pixels at or above this are treated as real data.
# Only more absurd sentinels (e.g. -9999, NaN, tagged nodata) go transparent.
_VALID_VALUE_MIN = -200.0


def _stretch_uint8(arr: np.ndarray, vmin: float, vmax: float, nodata=None) -> np.ndarray:
    a = arr.astype(np.float32)
    mask = _valid_mask(a, nodata)
    denom = (vmax - vmin) if vmax > vmin else 1.0
    out = (np.clip(a, vmin, vmax) - vmin) / denom * 255.0
    out = np.where(mask, out, 0)
    return out.astype(np.uint8)


def _valid_mask(arr: np.ndarray, nodata=None) -> np.ndarray:
    """True where pixel has usable data — not nodata / NaN / absurd sentinels."""
    a = arr.astype(np.float32)
    mask = np.isfinite(a)
    if nodata is not None and np.isfinite(nodata):
        mask &= a != float(nodata)
    # Keep real cold values (down to -200); drop classic fillers like -9999
    mask &= a >= _VALID_VALUE_MIN
    return mask


def _rgb_to_rgba(
    rgb: np.ndarray,
    valid: np.ndarray | None = None,
    *,
    zero_rgb_is_nodata: bool = False,
) -> Image.Image:
    """
    RGB uint8 HxWx3 → RGBA.

    Transparent when:
    - `valid` is False (float nodata / values < -200 / NaN), and/or
    - `zero_rgb_is_nodata` and all three bands are exactly 0 (common RGB ortho
      empty fill when the GeoTIFF has no nodata tag).

    Does NOT treat near-black (1..N) as empty — legitimate dark scene content
    stays opaque. Never use zero-fill on stretched thermal (vmin maps to 0).
    """
    if rgb.dtype != np.uint8:
        rgb = np.clip(np.nan_to_num(rgb, nan=0.0), 0, 255).astype(np.uint8)
    h, w = rgb.shape[:2]
    alpha = np.full((h, w), 255, dtype=np.uint8)
    if zero_rgb_is_nodata:
        alpha[(rgb[:, :, 0] == 0) & (rgb[:, :, 1] == 0) & (rgb[:, :, 2] == 0)] = 0
    if valid is not None:
        alpha[~valid] = 0
    return Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _empty_tile_png(size: int = 256) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return _png_bytes(img)


def tile_bounds_mercator(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web Mercator (EPSG:3857) bounds for XYZ tile: west, south, east, north."""
    n = 2.0 ** z
    origin = 20037508.342789244
    tile = 2 * origin / n
    west = -origin + x * tile
    east = -origin + (x + 1) * tile
    north = origin - y * tile
    south = origin - (y + 1) * tile
    return west, south, east, north


@lru_cache(maxsize=32)
def _display_stats(path_str: str, mtime_ns: int) -> dict[str, Any]:
    """Stable stretch params from an overview sample (cached per file revision)."""
    path = Path(path_str)
    with _safe_open_rasterio(path) as ds:
        from rasterio.enums import Resampling

        nodata = ds.nodata
        out_w = min(1024, ds.width)
        out_h = min(1024, ds.height)
        if ds.count >= 3:
            sample = ds.read(
                [1, 2, 3],
                out_shape=(3, out_h, out_w),
                resampling=Resampling.bilinear,
            )
            if sample.dtype == np.uint8:
                return {"mode": "uint8", "bands": 3, "nodata": nodata}
            bands_stats = []
            for i in range(3):
                a = sample[i].astype(np.float32)
                mask = _valid_mask(a, nodata)
                valid = a[mask]
                if valid.size:
                    lo, hi = np.percentile(valid, [2, 98])
                else:
                    lo, hi = 0.0, 1.0
                if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                    lo, hi = 0.0, 1.0
                bands_stats.append((float(lo), float(hi)))
            return {"mode": "rgb_stretch", "bands": 3, "stats": bands_stats, "nodata": nodata}

        sample = ds.read(1, out_shape=(out_h, out_w), resampling=Resampling.bilinear)
        a = sample.astype(np.float32)
        mask = _valid_mask(a, nodata)
        valid = a[mask]
        if valid.size:
            lo, hi = np.percentile(valid, [2, 98])
            vmin = float(np.min(valid))
            if vmin < _VALID_VALUE_MIN:
                valid2 = valid[valid >= _VALID_VALUE_MIN]
                if valid2.size:
                    lo, hi = np.percentile(valid2, [2, 98])
        else:
            lo, hi = 0.0, 1.0
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = 0.0, 1.0
        return {"mode": "thermal", "bands": 1, "vmin": float(lo), "vmax": float(hi), "nodata": nodata}


def get_display_stats(path: Path) -> dict[str, Any]:
    path = Path(path)
    st = path.stat()
    return _display_stats(str(path.resolve()), int(st.st_mtime_ns))


def render_geotiff_window(
    geotiff_path: Path,
    col_off: int,
    row_off: int,
    width: int,
    height: int,
    out_w: int,
    out_h: int,
) -> bytes:
    """
    Read a pixel window from the full GeoTIFF (using overviews when helpful)
    and return a PNG at out_w x out_h — sharp at any zoom.
    Nodata / absurd values (below -200) → alpha 0; real black stays opaque.
    """
    from rasterio.enums import Resampling
    from rasterio.windows import Window

    geotiff_path = Path(geotiff_path)
    stats = get_display_stats(geotiff_path)

    with _safe_open_rasterio(geotiff_path) as ds:
        col_off = int(max(0, min(col_off, ds.width - 1)))
        row_off = int(max(0, min(row_off, ds.height - 1)))
        width = int(max(1, min(width, ds.width - col_off)))
        height = int(max(1, min(height, ds.height - row_off)))
        out_w = int(max(1, min(out_w, 4096)))
        out_h = int(max(1, min(out_h, 4096)))

        window = Window(col_off, row_off, width, height)
        # Prefer nearest when magnifying so source pixels stay crisp
        resampling = Resampling.bilinear if (out_w < width or out_h < height) else Resampling.nearest
        nodata = stats.get("nodata")

        if stats.get("mode") == "uint8" or ds.count >= 3:
            data = ds.read(
                [1, 2, 3] if ds.count >= 3 else [1, 1, 1],
                window=window,
                out_shape=(3, out_h, out_w),
                resampling=resampling,
            )
            if stats.get("mode") == "uint8":
                rgb = np.transpose(data, (1, 2, 0))
                if rgb.dtype != np.uint8:
                    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
                valid = None
                if nodata is not None:
                    valid = (
                        _valid_mask(data[0], nodata)
                        | _valid_mask(data[1], nodata)
                        | _valid_mask(data[2], nodata)
                    )
                img = _rgb_to_rgba(rgb, valid, zero_rgb_is_nodata=True)
            else:
                band_stats = stats.get("stats") or [(0.0, 255.0)] * 3
                bands = [_stretch_uint8(data[i], lo, hi, nodata) for i, (lo, hi) in enumerate(band_stats)]
                rgb = np.stack(bands, axis=-1)
                valid = (
                    _valid_mask(data[0], nodata)
                    | _valid_mask(data[1], nodata)
                    | _valid_mask(data[2], nodata)
                )
                img = _rgb_to_rgba(rgb, valid, zero_rgb_is_nodata=False)
        else:
            data = ds.read(
                1,
                window=window,
                out_shape=(out_h, out_w),
                resampling=resampling,
            )
            gray = _stretch_uint8(
                data,
                float(stats.get("vmin", 0.0)),
                float(stats.get("vmax", 1.0)),
                nodata,
            )
            rgb = np.stack([gray, gray, gray], axis=-1)
            img = _rgb_to_rgba(rgb, _valid_mask(data, nodata), zero_rgb_is_nodata=False)

    return _png_bytes(img)


def _array_to_display_png(data: np.ndarray, stats: dict[str, Any]) -> Image.Image:
    """Convert reprojected array (C,H,W) or (H,W) to an RGBA PIL image."""
    nodata = stats.get("nodata")
    if data.ndim == 3 and data.shape[0] >= 3:
        if stats.get("mode") == "uint8":
            rgb = np.transpose(data[:3], (1, 2, 0))
            if rgb.dtype != np.uint8:
                rgb = np.clip(np.nan_to_num(rgb, nan=0.0), 0, 255).astype(np.uint8)
            else:
                rgb = np.nan_to_num(rgb, nan=0).astype(np.uint8)
            valid = _valid_mask(data[0], nodata) if nodata is not None else None
            return _rgb_to_rgba(rgb, valid, zero_rgb_is_nodata=True)
        band_stats = stats.get("stats") or [(0.0, 255.0)] * 3
        bands = [
            _stretch_uint8(data[i], lo, hi, nodata)
            for i, (lo, hi) in enumerate(band_stats[:3])
        ]
        rgb = np.stack(bands, axis=-1)
        valid = (
            _valid_mask(data[0], nodata)
            | _valid_mask(data[1], nodata)
            | _valid_mask(data[2], nodata)
        )
        return _rgb_to_rgba(rgb, valid, zero_rgb_is_nodata=False)

    raw = data if data.ndim == 2 else data[0]
    gray = _stretch_uint8(
        raw,
        float(stats.get("vmin", 0.0)),
        float(stats.get("vmax", 1.0)),
        nodata,
    )
    rgb = np.stack([gray, gray, gray], axis=-1)
    return _rgb_to_rgba(rgb, _valid_mask(raw, nodata), zero_rgb_is_nodata=False)


def render_geotiff_geo_window(
    geotiff_path: Path,
    west: float,
    south: float,
    east: float,
    north: float,
    out_w: int,
    out_h: int,
) -> bytes:
    """
    Render a geographic bbox to PNG on an *exact* destination grid.

    Uses WarpedVRT/reproject so the output covers (west,south,east,north) precisely.
    (The old integer-window + stretch approach drifted when zoomed out.)
    """
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds as affine_from_bounds
    from rasterio.vrt import WarpedVRT

    geotiff_path = Path(geotiff_path)
    stats = get_display_stats(geotiff_path)
    out_w = int(max(1, min(out_w, 4096)))
    out_h = int(max(1, min(out_h, 4096)))
    if east == west or north == south:
        raise ValueError("Invalid geographic window")

    dst_transform = affine_from_bounds(west, south, east, north, out_w, out_h)

    with _safe_open_rasterio(geotiff_path) as ds:
        vrt_options = dict(
            transform=dst_transform,
            width=out_w,
            height=out_h,
            resampling=Resampling.bilinear,
        )
        if ds.crs is not None:
            vrt_options["crs"] = ds.crs
        with WarpedVRT(ds, **vrt_options) as vrt:
            if ds.count >= 3:
                data = vrt.read([1, 2, 3])
            else:
                data = vrt.read(1)

    img = _array_to_display_png(data, stats)
    return _png_bytes(img)


def render_geotiff_matched_to_ref_window(
    geotiff_path: Path,
    ref_path: Path,
    col_off: int,
    row_off: int,
    width: int,
    height: int,
    out_w: int,
    out_h: int,
) -> bytes:
    """
    Reproject geotiff onto the exact geographic grid of a reference (RGB) pixel window.

    Guarantees pixel-aligned overlay with render_geotiff_window(ref, same args).
    """
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window
    from rasterio.windows import transform as window_transform

    geotiff_path = Path(geotiff_path)
    ref_path = Path(ref_path)
    stats = get_display_stats(geotiff_path)
    out_w = int(max(1, min(out_w, 4096)))
    out_h = int(max(1, min(out_h, 4096)))

    with _safe_open_rasterio(ref_path) as ref:
        col_off = int(max(0, min(col_off, ref.width - 1)))
        row_off = int(max(0, min(row_off, ref.height - 1)))
        width = int(max(1, min(width, ref.width - col_off)))
        height = int(max(1, min(height, ref.height - row_off)))
        win = Window(col_off, row_off, width, height)
        # Affine for one source pixel of the RGB window → scale to out resolution
        win_transform = window_transform(win, ref.transform)
        dst_transform = win_transform * Affine.scale(width / out_w, height / out_h)
        ref_crs = ref.crs

    with _safe_open_rasterio(geotiff_path) as ds:
        vrt_options: dict[str, Any] = dict(
            transform=dst_transform,
            width=out_w,
            height=out_h,
            resampling=Resampling.bilinear,
        )
        # Prefer reference CRS so both overlays share the same grid space
        crs = ref_crs or ds.crs
        if crs is not None:
            vrt_options["crs"] = crs
        with WarpedVRT(ds, **vrt_options) as vrt:
            if ds.count >= 3:
                data = vrt.read([1, 2, 3])
            else:
                data = vrt.read(1)

    img = _array_to_display_png(data, stats)
    return _png_bytes(img)


def create_preview_png(
    geotiff_path: Path,
    out_png: Path,
    max_dim: int = 2048,
) -> dict[str, Any]:
    """Legacy overview PNG (still used for thumbnails only — maps use XYZ tiles)."""
    geotiff_path = Path(geotiff_path)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    with _safe_open_rasterio(geotiff_path) as ds:
        scale = max(ds.width, ds.height) / max_dim
        out_w = max(1, int(math.ceil(ds.width / max(scale, 1))))
        out_h = max(1, int(math.ceil(ds.height / max(scale, 1))))
        png = render_geotiff_window(geotiff_path, 0, 0, ds.width, ds.height, out_w, out_h)
        out_png.write_bytes(png)
        bounds = ds.bounds
        crs = ds.crs.to_string() if ds.crs else None

    return {
        "png": str(out_png),
        "width": out_w,
        "height": out_h,
        "crs": crs,
        "bounds": {
            "left": bounds.left,
            "bottom": bounds.bottom,
            "right": bounds.right,
            "top": bounds.top,
        },
    }


def estimate_maxzoom(geotiff_path: Path) -> int:
    """Zoom level where a map tile ≈ native GeoTIFF pixel size (Web Mercator)."""
    geotiff_path = Path(geotiff_path)
    with _safe_open_rasterio(geotiff_path) as ds:
        # Pixel size in CRS units
        px = abs(ds.transform.a)
        # Rough convert to meters if geographic degrees
        crs = ds.crs
        if crs and getattr(crs, "is_geographic", False):
            # ~111km per degree at mid-latitudes; use center latitude
            lat = (ds.bounds.top + ds.bounds.bottom) / 2
            meters = px * 111_320.0 * max(0.2, math.cos(math.radians(lat)))
        else:
            meters = px if px > 0 else 0.05
        # WebMercator resolution at z: 156543.03 / 2^z meters/pixel (at equator)
        if meters <= 0:
            return 22
        z = math.log2(156543.03392804097 / meters)
        return int(max(14, min(24, math.ceil(z) + 1)))


def render_geotiff_xyz_tile(
    geotiff_path: Path,
    z: int,
    x: int,
    y: int,
    *,
    size: int = 256,
) -> bytes:
    """
    Render one MapLibre XYZ tile from the full GeoTIFF (EPSG:3857).

    Reads the native raster (no pre-baked downsample). When the map is zoomed
    past native GSD, uses nearest-neighbour so pixels stay sharp.
    Nodata / absurd sentinels (e.g. below -200, -9999) are transparent (RGBA).
    Legitimate dark / black scene content stays opaque.
    """
    geotiff_path = Path(geotiff_path)
    try:
        mtime = int(geotiff_path.stat().st_mtime_ns)
    except OSError:
        mtime = 0
    return _render_xyz_tile_cached(str(geotiff_path.resolve()), mtime, int(z), int(x), int(y), int(size))


@lru_cache(maxsize=512)
def _render_xyz_tile_cached(
    path_str: str,
    mtime_ns: int,
    z: int,
    x: int,
    y: int,
    size: int,
) -> bytes:
    _ = mtime_ns
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds as affine_from_bounds
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import transform_bounds

    geotiff_path = Path(path_str)
    size = int(max(64, min(size, 512)))
    if z < 0 or z > 28 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        return _empty_tile_png(size)

    west, south, east, north = tile_bounds_mercator(z, x, y)
    stats = get_display_stats(geotiff_path)
    dst_crs = CRS.from_epsg(3857)
    dst_transform = affine_from_bounds(west, south, east, north, size, size)

    with _safe_open_rasterio(geotiff_path) as ds:
        src_crs = ds.crs or dst_crs
        try:
            left, bottom, right, top = transform_bounds(
                src_crs, dst_crs, *ds.bounds, densify_pts=21
            )
        except Exception:
            left, bottom, right, top = ds.bounds
        if east < left or west > right or north < bottom or south > top:
            return _empty_tile_png(size)

        tile_mpp = (east - west) / size
        src_mpp = abs(ds.transform.a)
        if src_crs and getattr(src_crs, "is_geographic", False):
            lat = (ds.bounds.top + ds.bounds.bottom) / 2
            src_mpp = src_mpp * 111_320.0 * max(0.2, math.cos(math.radians(lat)))
        resampling = Resampling.nearest if tile_mpp < src_mpp * 0.95 else Resampling.bilinear

        nodata = ds.nodata
        vrt_kw: dict[str, Any] = dict(
            crs=dst_crs,
            transform=dst_transform,
            width=size,
            height=size,
            resampling=resampling,
        )
        if nodata is not None:
            vrt_kw["nodata"] = nodata
        with WarpedVRT(ds, **vrt_kw) as vrt:
            if ds.count >= 3:
                data = vrt.read([1, 2, 3])
            else:
                data = vrt.read(1)

    return _png_bytes(_array_to_display_png(data, stats))
