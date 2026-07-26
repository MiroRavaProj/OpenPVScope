"""Register a thermal GeoTIFF to an RGB GeoTIFF.

Self-contained: no imports from the parent PoC package.

Pipeline:
  Pass 1 — tile phase + AKAZE → RANSAC affine, ECC refine (``WARP_INVERSE_MAP``)
  Pass 2 — metre-tile phase → TPS (local clamp ``LOCAL_MAX_M``)

- RGB is never warped (reference only).
- Registration runs on a working grid from the thermal, capped at
  ``MAX_REG_GSD_M`` (2 cm/px).
- Writes native-thermal aligned GeoTIFF plus ``*_pass1.tif`` (affine only).
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import reproject
from scipy.interpolate import RBFInterpolator

# ---------------------------------------------------------------------------
# Tuned constants (metres)
# ---------------------------------------------------------------------------
NODATA = np.float32(-32767.0)
GLOBAL_MAX_M = 2.5
LOCAL_MAX_M = 0.5
TILE_M = 5.76  # former 192 px @ ~3 cm
STRIDE_M = 3.84  # former 128 px @ ~3 cm
# Finest GSD used for registration (never process finer than this)
MAX_REG_GSD_M = 0.02


def estimate_gsd_m(transform, bounds=None, lat: float | None = None) -> float:
    """Approx metres/pixel from an affine geotransform (EPSG:4326-friendly)."""
    if lat is None:
        if bounds is not None:
            lat = 0.5 * (float(bounds.top) + float(bounds.bottom))
        else:
            lat = float(transform.f)
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
    m_per_deg_lat = 110540.0
    gsd = 0.5 * (abs(transform.a) * m_per_deg_lon + abs(transform.e) * m_per_deg_lat)
    if not np.isfinite(gsd) or gsd <= 0:
        raise ValueError(f"Could not estimate GSD from transform (got {gsd})")
    return float(gsd)


def meters_to_px(meters: float, gsd_m: float, min_px: int = 16) -> int:
    if gsd_m is None or not np.isfinite(gsd_m) or gsd_m <= 0:
        raise ValueError(f"gsd_m must be a positive finite value, got {gsd_m}")
    return max(min_px, int(round(float(meters) / float(gsd_m))))


def max_displacement_px(gsd_m: float, max_m: float) -> float:
    return float(max_m / float(gsd_m))


def clamp_flow(flow: np.ndarray, max_px: float) -> np.ndarray:
    max_px = float(max_px)
    mag = np.linalg.norm(flow, axis=-1, keepdims=True)
    out = flow.astype(np.float32).copy()
    over = mag[..., 0] > max_px
    if np.any(over):
        scale = (max_px / (mag[..., 0][over] + 1e-8)).astype(np.float32)
        out[over, 0] *= scale
        out[over, 1] *= scale
    return out


def clamp_shift_xy(sx: float, sy: float, max_px: float) -> tuple[float, float]:
    mag = float(np.hypot(sx, sy))
    if mag > max_px and mag > 0:
        s = max_px / mag
        return sx * s, sy * s
    return sx, sy


def to_uint8_norm(img: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    x = img.astype(np.float32)
    if mask is not None:
        valid = mask & np.isfinite(x)
    else:
        valid = np.isfinite(x)
    if not np.any(valid):
        return np.zeros(x.shape, dtype=np.uint8)
    lo, hi = np.percentile(x[valid], (2, 98))
    if hi <= lo:
        hi = lo + 1.0
    y = np.clip((x - lo) / (hi - lo), 0, 1)
    y = (y * 255.0).astype(np.uint8)
    if mask is not None:
        y[~mask] = 0
    return y


def gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def flow_from_translation(dx: float, dy: float, h: int, w: int) -> np.ndarray:
    flow = np.zeros((h, w, 2), dtype=np.float32)
    flow[..., 0] = dx
    flow[..., 1] = dy
    return flow


def upsample_flow(flow: np.ndarray, out_hw: tuple[int, int]) -> np.ndarray:
    """Upsample dense flow and scale displacements to the finer grid."""
    h, w = out_hw
    fh, fw = flow.shape[:2]
    if fh == h and fw == w:
        return flow.astype(np.float32)
    sy, sx = h / fh, w / fw
    up = cv2.resize(flow, (w, h), interpolation=cv2.INTER_LINEAR)
    up[..., 0] *= sx
    up[..., 1] *= sy
    return up.astype(np.float32)


def apply_flow(src: np.ndarray, flow: np.ndarray, nodata: float = NODATA) -> np.ndarray:
    """Warp source with dense flow: out[p] = src[p + flow]."""
    h, w = src.shape
    grid_x, grid_y = np.meshgrid(
        np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32)
    )
    map_x = (grid_x + flow[..., 0]).astype(np.float32)
    map_y = (grid_y + flow[..., 1]).astype(np.float32)
    work = src.copy()
    invalid = (~np.isfinite(work)) | (work == nodata)
    work[invalid] = 0.0
    warped = cv2.remap(
        work,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    mask_src = (~invalid).astype(np.uint8) * 255
    mask_w = cv2.remap(
        mask_src,
        map_x,
        map_y,
        interpolation=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    out = warped.astype(np.float32)
    out[mask_w < 128] = nodata
    return out


def _create_akaze():
    if hasattr(cv2, "AKAZE_create"):
        return cv2.AKAZE_create()
    if hasattr(cv2, "xfeatures2d_AKAZE"):
        return cv2.xfeatures2d_AKAZE.create()
    return cv2.ORB_create(nfeatures=5000)


def match_features_akaze(
    img_ref_u8: np.ndarray,
    img_mov_u8: np.ndarray,
    max_matches: int = 4000,
) -> tuple[np.ndarray, np.ndarray]:
    detector = _create_akaze()
    k1, d1 = detector.detectAndCompute(img_ref_u8, None)
    k2, d2 = detector.detectAndCompute(img_mov_u8, None)
    if d1 is None or d2 is None or len(k1) < 4 or len(k2) < 4:
        return np.zeros((0, 2), np.float64), np.zeros((0, 2), np.float64)
    norm = cv2.NORM_HAMMING if d1.dtype == np.uint8 else cv2.NORM_L2
    pairs = cv2.BFMatcher(norm).knnMatch(d1, d2, k=2)
    good = []
    for m_n in pairs:
        if len(m_n) < 2:
            continue
        m, n = m_n
        if m.distance < 0.75 * n.distance:
            good.append(m)
    good.sort(key=lambda m: m.distance)
    good = good[:max_matches]
    if len(good) < 4:
        return np.zeros((0, 2), np.float64), np.zeros((0, 2), np.float64)
    ref_pts = np.float64([k1[m.queryIdx].pt for m in good])
    mov_pts = np.float64([k2[m.trainIdx].pt for m in good])
    return ref_pts, mov_pts


def apply_affine(src: np.ndarray, M: np.ndarray, nodata: float = NODATA) -> np.ndarray:
    """Warp float image with 2x3 affine (OpenCV ECC / estimateAffine2D convention).

    Uses ``WARP_INVERSE_MAP`` so ``M`` from ``estimateAffine2D(ref_pts, mov_pts)``
    and ``findTransformECC`` aligns mov→ref.
    """
    h, w = src.shape[:2]
    m = np.asarray(M, dtype=np.float32).reshape(2, 3)
    flags = cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP
    work = src.copy()
    invalid = (~np.isfinite(work)) | (work == nodata)
    work[invalid] = 0.0
    warped = cv2.warpAffine(
        work, m, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0
    )
    mask = (~invalid).astype(np.uint8) * 255
    mask_w = cv2.warpAffine(
        mask,
        m,
        (w, h),
        flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    out = warped.astype(np.float32)
    out[mask_w < 128] = nodata
    return out


def affine_params(M: np.ndarray) -> dict[str, float]:
    a, b, tx = float(M[0, 0]), float(M[0, 1]), float(M[0, 2])
    c, d, ty = float(M[1, 0]), float(M[1, 1]), float(M[1, 2])
    scale_x = math.hypot(a, c)
    scale_y = math.hypot(b, d)
    rot_deg = math.degrees(math.atan2(c, a)) if scale_x > 1e-12 else 0.0
    return {
        "scale_x": scale_x,
        "scale_y": scale_y,
        "rotation_deg": rot_deg,
        "tx": tx,
        "ty": ty,
    }


def rescale_affine_matrix(
    M: np.ndarray, src_hw: tuple[int, int], dst_hw: tuple[int, int]
) -> np.ndarray:
    """Rescale work-grid affine to another pixel grid (same geo framing)."""
    sh, sw = src_hw
    dh, dw = dst_hw
    rx = dw / float(sw)
    ry = dh / float(sh)
    m = np.asarray(M, dtype=np.float64).reshape(2, 3).copy()
    m[0, 2] *= rx
    m[1, 2] *= ry
    if abs(rx - ry) > 1e-9:
        m[0, 1] *= rx / ry
        m[1, 0] *= ry / rx
    return m.astype(np.float32)


def _tile_phase_pairs_for_affine(
    ref_u8: np.ndarray,
    mov_u8: np.ndarray,
    tile: int,
    stride: int,
    max_shift: float,
    min_response: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """Tile phase pairs as (dst=ref, src=mov sample) for estimateAffine2D."""
    h, w = ref_u8.shape
    dst_pts: list[list[float]] = []
    src_pts: list[list[float]] = []
    for y0 in range(0, h - tile + 1, stride):
        for x0 in range(0, w - tile + 1, stride):
            r = ref_u8[y0 : y0 + tile, x0 : x0 + tile]
            m = mov_u8[y0 : y0 + tile, x0 : x0 + tile]
            if r.std() < 8 or m.std() < 8:
                continue
            shift, resp = cv2.phaseCorrelate(m.astype(np.float32), r.astype(np.float32))
            sx, sy = float(shift[0]), float(shift[1])
            if resp < min_response:
                continue
            if abs(sx) > max_shift or abs(sy) > max_shift:
                continue
            cx = x0 + tile / 2.0
            cy = y0 + tile / 2.0
            dst_pts.append([cx, cy])
            src_pts.append([cx - sx, cy - sy])
    if not dst_pts:
        return np.zeros((0, 2), np.float64), np.zeros((0, 2), np.float64)
    return np.asarray(dst_pts, np.float64), np.asarray(src_pts, np.float64)


def estimate_global_affine_akaze(
    ref_u8: np.ndarray,
    mov_u8: np.ndarray,
    max_shift_px: float | None = None,
    match_max_side: int = 2048,
    gsd_m: float | None = None,
    tile_m: float = TILE_M,
    stride_m: float = STRIDE_M,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Pass-1: dense tile-phase + AKAZE → RANSAC affine, then ECC refine.

    No hard scale clamps. Tile phase uses a *generous* shift budget so bottom-of-
    plant scale residuals can pull the affine (unlike Pass-2's 0.5 m clamp).
    """
    h, w = ref_u8.shape
    mside = max(h, w)
    if mside > match_max_side:
        f = match_max_side / mside
        ref_s = cv2.resize(ref_u8, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
        mov_s = cv2.resize(mov_u8, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
    else:
        f = 1.0
        ref_s, mov_s = ref_u8, mov_u8
    hs, ws = ref_s.shape

    # Generous Pass-1 shift: allow ~15% of frame (scale shows up as large edge shift)
    pass1_max = 0.15 * float(min(hs, ws))
    if max_shift_px is not None:
        pass1_max = max(pass1_max, float(max_shift_px) * f)

    if gsd_m is not None and gsd_m > 0:
        tile = max(32, int(round(float(tile_m) / gsd_m * f)))
        stride = max(16, int(round(float(stride_m) / gsd_m * f)))
    else:
        tile = max(32, min(hs, ws) // 16)
        stride = max(16, tile * 2 // 3)
    if stride >= tile:
        stride = max(8, tile // 2)

    dst_tile, src_tile = _tile_phase_pairs_for_affine(
        ref_s, mov_s, tile=tile, stride=stride, max_shift=pass1_max
    )
    ref_ak, mov_ak = match_features_akaze(ref_s, mov_s)

    parts_d, parts_s = [], []
    if len(dst_tile):
        parts_d.append(dst_tile)
        parts_s.append(src_tile)
    if len(ref_ak):
        parts_d.append(ref_ak)
        parts_s.append(mov_ak)

    meta: dict[str, Any] = {
        "pass1": "tile+akaze_affine",
        "n_tile_pairs": int(len(dst_tile)),
        "n_akaze_matches": int(len(ref_ak)),
        "n_matches": 0,
        "n_inliers": 0,
        "match_scale": float(f),
        "pass1_max_shift_px_proxy": float(pass1_max),
        "ecc_cc": None,
    }

    M = None
    if parts_d:
        dst = np.vstack(parts_d)
        src = np.vstack(parts_s)
        meta["n_matches"] = int(len(dst))
        if len(dst) >= 6:
            M, mask = cv2.estimateAffine2D(
                dst.astype(np.float32),
                src.astype(np.float32),
                method=cv2.RANSAC,
                ransacReprojThreshold=5.0,
                maxIters=8000,
                confidence=0.995,
            )
            if M is not None and mask is not None:
                meta["n_inliers"] = int(mask.ravel().sum())
                if meta["n_inliers"] < 6:
                    M = None

    if M is not None:
        M = np.asarray(M, dtype=np.float32)
        # ECC refine on proxy (absorbs residual stretch AKAZE missed)
        try:
            criteria = (
                cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                80,
                1e-6,
            )
            cc, M_ecc = cv2.findTransformECC(
                ref_s.astype(np.float32) / 255.0,
                mov_s.astype(np.float32) / 255.0,
                M.copy(),
                cv2.MOTION_AFFINE,
                criteria,
                None,
                5,
            )
            M = np.asarray(M_ecc, dtype=np.float32)
            meta["ecc_cc"] = float(cc)
            meta["pass1"] = "tile+akaze+ecc_affine"
        except cv2.error:
            pass
        if f != 1.0:
            M[0, 2] /= f
            M[1, 2] /= f
        meta.update(affine_params(M))
        return M, meta

    # Fallback: phase translation only
    shift, response = cv2.phaseCorrelate(
        mov_u8.astype(np.float32), ref_u8.astype(np.float32)
    )
    sx, sy = float(shift[0]), float(shift[1])
    if max_shift_px is not None:
        sx, sy = clamp_shift_xy(sx, sy, float(max_shift_px))
    M = np.float32([[1.0, 0.0, -sx], [0.0, 1.0, -sy]])
    meta.update(
        {
            "pass1": "phase_fallback",
            "phase_response": float(response),
            "phase_shift_xy": [sx, sy],
        }
    )
    meta.update(affine_params(M))
    return M, meta


def tile_phase_correspondences(
    ref_u8: np.ndarray,
    mov_u8: np.ndarray,
    tile: int,
    stride: int,
    max_shift: float,
    min_response: float = 0.05,
) -> tuple[np.ndarray, np.ndarray, int]:
    h, w = ref_u8.shape
    dst_pts: list[list[float]] = []
    src_pts: list[list[float]] = []
    for y0 in range(0, h - tile + 1, stride):
        for x0 in range(0, w - tile + 1, stride):
            r = ref_u8[y0 : y0 + tile, x0 : x0 + tile]
            m = mov_u8[y0 : y0 + tile, x0 : x0 + tile]
            if r.std() < 8 or m.std() < 8:
                continue
            shift, resp = cv2.phaseCorrelate(m.astype(np.float32), r.astype(np.float32))
            sx, sy = float(shift[0]), float(shift[1])
            if resp < min_response:
                continue
            if abs(sx) > max_shift or abs(sy) > max_shift:
                continue
            cx = x0 + tile / 2.0
            cy = y0 + tile / 2.0
            dst_pts.append([cx, cy])
            src_pts.append([cx - sx, cy - sy])
    if not dst_pts:
        return np.zeros((0, 2), np.float64), np.zeros((0, 2), np.float64), 0
    return (
        np.asarray(dst_pts, np.float64),
        np.asarray(src_pts, np.float64),
        len(dst_pts),
    )


def fit_tps_flow(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
    h: int,
    w: int,
    grid_step: int = 32,
) -> np.ndarray:
    if len(src_pts) < 3:
        return np.zeros((h, w, 2), dtype=np.float32)

    rbf_x = RBFInterpolator(
        dst_pts, src_pts[:, 0], kernel="thin_plate_spline", smoothing=1.0
    )
    rbf_y = RBFInterpolator(
        dst_pts, src_pts[:, 1], kernel="thin_plate_spline", smoothing=1.0
    )
    ys = np.arange(0, h, grid_step, dtype=np.float64)
    xs = np.arange(0, w, grid_step, dtype=np.float64)
    if ys[-1] != h - 1:
        ys = np.append(ys, h - 1)
    if xs[-1] != w - 1:
        xs = np.append(xs, w - 1)
    grid_x, grid_y = np.meshgrid(xs, ys)
    query = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    pred_x = rbf_x(query).reshape(grid_y.shape)
    pred_y = rbf_y(query).reshape(grid_y.shape)
    flow_small = np.stack(
        [(pred_x - grid_x).astype(np.float32), (pred_y - grid_y).astype(np.float32)],
        axis=-1,
    )
    full = np.zeros((h, w, 2), dtype=np.float32)
    full[..., 0] = cv2.resize(flow_small[..., 0], (w, h), interpolation=cv2.INTER_LINEAR)
    full[..., 1] = cv2.resize(flow_small[..., 1], (w, h), interpolation=cv2.INTER_LINEAR)
    return full


def estimate_tile_tps_flow(
    ref_u8: np.ndarray,
    mov_u8: np.ndarray,
    tile: int,
    stride: int,
    max_shift: float,
) -> tuple[np.ndarray, int]:
    h, w = ref_u8.shape
    dst_pts, src_pts, n = tile_phase_correspondences(
        ref_u8, mov_u8, tile=tile, stride=stride, max_shift=max_shift
    )
    if n >= 8:
        dst_use, src_use = dst_pts, src_pts
        if n > 400:
            idx = np.linspace(0, n - 1, 400).astype(int)
            dst_use, src_use = dst_pts[idx], src_pts[idx]
        flow = fit_tps_flow(src_use, dst_use, h, w, grid_step=max(32, tile // 16))
    else:
        shift, _ = cv2.phaseCorrelate(
            mov_u8.astype(np.float32), ref_u8.astype(np.float32)
        )
        flow = flow_from_translation(-float(shift[0]), -float(shift[1]), h, w)
    return flow, n


def _fill_nodata(arr: np.ndarray, nodata: float) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(arr) & (arr != nodata)
    out = arr.copy()
    if np.any(valid):
        out[~valid] = float(np.median(arr[valid]))
    else:
        out[:] = 0.0
    return out, valid


def _reproject_rgb_gray_to_grid(
    rgb_path: Path,
    dst_transform,
    dst_crs,
    dst_height: int,
    dst_width: int,
) -> np.ndarray:
    """Reproject RGB luminance onto an arbitrary destination grid."""
    dst = np.zeros((dst_height, dst_width), dtype=np.float32)
    with rasterio.open(rgb_path) as rgb:
        count = min(3, rgb.count)
        if count == 1:
            reproject(
                source=rasterio.band(rgb, 1),
                destination=dst,
                src_transform=rgb.transform,
                src_crs=rgb.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
            return dst
        weights = (0.299, 0.587, 0.114)
        tmp = np.empty((dst_height, dst_width), dtype=np.float32)
        for i, w in enumerate(weights[:count], start=1):
            reproject(
                source=rasterio.band(rgb, i),
                destination=tmp,
                src_transform=rgb.transform,
                src_crs=rgb.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
            dst += w * tmp
    return dst


def _reproject_band_to_grid(
    src_path: Path,
    dst_transform,
    dst_crs,
    dst_height: int,
    dst_width: int,
    nodata: float,
    band: int = 1,
) -> np.ndarray:
    dst = np.full((dst_height, dst_width), nodata, dtype=np.float32)
    with rasterio.open(src_path) as src:
        src_nodata = src.nodata if src.nodata is not None else nodata
        reproject(
            source=rasterio.band(src, band),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=src_nodata,
            dst_nodata=nodata,
        )
    return dst


def _build_working_grid_capped(
    therm_ds: rasterio.DatasetReader, max_reg_gsd_m: float
) -> tuple[Any, Any, int, int, float, float]:
    """Thermal-based working grid, never finer than ``max_reg_gsd_m``.

    Returns (work_transform, work_crs, work_h, work_w, gsd_therm, gsd_work).
    """
    gsd_therm = estimate_gsd_m(therm_ds.transform, therm_ds.bounds)
    gsd_work = max(float(gsd_therm), float(max_reg_gsd_m))
    if gsd_work <= gsd_therm * 1.001:
        return (
            therm_ds.transform,
            therm_ds.crs,
            therm_ds.height,
            therm_ds.width,
            gsd_therm,
            gsd_therm,
        )
    scale = gsd_work / gsd_therm
    work_transform = therm_ds.transform * Affine.scale(scale, scale)
    work_h = max(1, int(round(therm_ds.height / scale)))
    work_w = max(1, int(round(therm_ds.width / scale)))
    return work_transform, therm_ds.crs, work_h, work_w, gsd_therm, gsd_work


def write_thermal_geotiff(
    path: Path, data: np.ndarray, profile: dict, nodata: float
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = profile.copy()
    profile.update(
        driver="GTiff",
        dtype="float32",
        count=1,
        nodata=nodata,
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
        height=int(data.shape[0]),
        width=int(data.shape[1]),
    )
    for k in ("photometric", "interleave"):
        profile.pop(k, None)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(np.float32), 1)


def register_thermal_to_rgb(
    rgb_path: str | Path,
    thermal_path: str | Path,
    output_path: str | Path,
    *,
    nodata: float = float(NODATA),
    max_reg_gsd_m: float = MAX_REG_GSD_M,
    global_max_m: float = GLOBAL_MAX_M,
    local_max_m: float = LOCAL_MAX_M,
    tile_m: float = TILE_M,
    stride_m: float = STRIDE_M,
) -> dict[str, Any]:
    """Align thermal to RGB; run registration at ≤ ``max_reg_gsd_m`` GSD.

    Output is native-resolution thermal (original thermal grid/CRS), content-warped
    so it lines up with RGB in map space. RGB is never modified.
    """
    t0 = time.perf_counter()
    rgb_path = Path(rgb_path)
    thermal_path = Path(thermal_path)
    output_path = Path(output_path)
    nodata_f = float(nodata)
    max_reg_gsd_m_f = float(max_reg_gsd_m)
    global_max_m_f = float(global_max_m)
    local_max_m_f = float(local_max_m)
    tile_m_f = float(tile_m)
    stride_m_f = float(stride_m)

    with rasterio.open(thermal_path) as therm:
        profile = therm.profile.copy()
        work_transform, work_crs, wh, ww, gsd_therm, gsd_work = _build_working_grid_capped(
            therm, max_reg_gsd_m_f
        )
        th_native = therm.read(1).astype(np.float32)
        native_h, native_w = therm.height, therm.width
        src_nodata = therm.nodata if therm.nodata is not None else nodata_f

    # Working-resolution stacks (thermal-sized or coarser, never finer than 2 cm)
    t_prep0 = time.perf_counter()
    rgb_work = _reproject_rgb_gray_to_grid(rgb_path, work_transform, work_crs, wh, ww)
    th_work = _reproject_band_to_grid(
        thermal_path, work_transform, work_crs, wh, ww, nodata_f
    )
    t_prep = time.perf_counter() - t_prep0

    th_fill, _ = _fill_nodata(th_work, nodata_f)
    ref = to_uint8_norm(gradient_magnitude(rgb_work))
    mov = to_uint8_norm(gradient_magnitude(th_fill))
    del rgb_work

    g_max = max_displacement_px(gsd_work, global_max_m_f)
    l_max = max_displacement_px(gsd_work, local_max_m_f)
    tile = meters_to_px(tile_m_f, gsd_work)
    stride = max(8, meters_to_px(stride_m_f, gsd_work, min_px=8))
    if stride >= tile:
        stride = max(8, tile // 2)

    # Pass 1 — tile-phase + AKAZE + ECC affine (no scale clamps)
    t_pass1 = time.perf_counter()
    M_work, pass1_meta = estimate_global_affine_akaze(
        ref,
        mov,
        max_shift_px=g_max,
        gsd_m=gsd_work,
        tile_m=tile_m_f,
        stride_m=stride_m_f,
    )
    th_phase = apply_affine(th_work, M_work, nodata=nodata_f)
    del th_work, mov
    t_pass1 = time.perf_counter() - t_pass1

    th_phase_fill, _ = _fill_nodata(th_phase, nodata_f)
    mov_fresh = to_uint8_norm(gradient_magnitude(th_phase_fill))
    del th_fill, th_phase_fill

    # Pass 2 — metre-tile TPS residuals (still ≤ 0.5 m)
    t_tps0 = time.perf_counter()
    flow_local, ctrl_pts = estimate_tile_tps_flow(
        ref, mov_fresh, tile=tile, stride=stride, max_shift=0.9 * l_max
    )
    del ref, mov_fresh
    flow_local = clamp_flow(flow_local, max_px=l_max)
    t_tps = time.perf_counter() - t_tps0

    # Apply same global affine (+ optional TPS) at native thermal res
    if src_nodata is not None:
        th_native = np.where(
            (~np.isfinite(th_native)) | (th_native == src_nodata), nodata_f, th_native
        )
    M_native = rescale_affine_matrix(M_work, (wh, ww), (native_h, native_w))
    th_native_pass1 = apply_affine(th_native, M_native, nodata=nodata_f)
    del th_native
    flow_native = upsample_flow(flow_local, (native_h, native_w))
    del flow_local
    aligned = apply_flow(th_native_pass1, flow_native, nodata_f)
    del flow_native

    profile.update(dtype="float32", count=1, nodata=nodata_f)
    write_thermal_geotiff(output_path, aligned, profile, nodata_f)
    # Pass-1-only native thermal (for UI debug)
    pass1_path = output_path.with_name(output_path.stem + "_pass1" + output_path.suffix)
    write_thermal_geotiff(pass1_path, th_native_pass1, profile, nodata_f)
    del th_native_pass1, th_phase

    meta: dict[str, Any] = {
        "rgb_path": str(rgb_path.resolve()),
        "thermal_path": str(thermal_path.resolve()),
        "output_path": str(output_path.resolve()),
        "method": "akaze_tps",
        "output_grid": "native_thermal",
        "gsd_thermal_m": gsd_therm,
        "gsd_work_m": gsd_work,
        "max_reg_gsd_m": max_reg_gsd_m_f,
        "work_shape_hw": [int(wh), int(ww)],
        "native_shape_hw": [int(native_h), int(native_w)],
        "prep_reproject_s": t_prep,
        "global_pass1_s": t_pass1,
        "tile_tps_s": t_tps,
        "affine_2x3_work": np.asarray(M_work).tolist(),
        "pass1_output_path": str(pass1_path.resolve()),
        "global_max_m": global_max_m_f,
        "local_max_m": local_max_m_f,
        "tile_m": tile_m_f,
        "stride_m": stride_m_f,
        "tile_px": tile,
        "stride_px": stride,
        "ctrl_pts": int(ctrl_pts),
        "runtime_s": float(time.perf_counter() - t0),
        "nodata": nodata_f,
        **pass1_meta,
    }
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Register thermal to RGB (AKAZE/affine Pass 1 + metre-tile TPS Pass 2); "
            "write native-resolution aligned thermal and *_pass1.tif."
        )
    )
    parser.add_argument("rgb", type=Path, help="Reference RGB GeoTIFF")
    parser.add_argument("thermal", type=Path, help="Thermal GeoTIFF to align")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output path for aligned thermal GeoTIFF (native thermal grid)",
    )
    parser.add_argument(
        "--nodata",
        type=float,
        default=float(NODATA),
        help=f"Nodata value (default {float(NODATA)})",
    )
    parser.add_argument(
        "--max-reg-gsd-m",
        type=float,
        default=MAX_REG_GSD_M,
        help=f"Finest registration GSD in metres (default {MAX_REG_GSD_M})",
    )
    parser.add_argument("--global-max-m", type=float, default=GLOBAL_MAX_M)
    parser.add_argument("--local-max-m", type=float, default=LOCAL_MAX_M)
    parser.add_argument("--tile-m", type=float, default=TILE_M)
    parser.add_argument("--stride-m", type=float, default=STRIDE_M)
    parser.add_argument(
        "--meta-json",
        type=Path,
        default=None,
        help="Optional path to write result metadata as JSON",
    )
    args = parser.parse_args(argv)

    meta = register_thermal_to_rgb(
        args.rgb,
        args.thermal,
        args.output,
        nodata=args.nodata,
        max_reg_gsd_m=args.max_reg_gsd_m,
        global_max_m=args.global_max_m,
        local_max_m=args.local_max_m,
        tile_m=args.tile_m,
        stride_m=args.stride_m,
    )
    if args.meta_json is not None:
        import json

        args.meta_json.parent.mkdir(parents=True, exist_ok=True)
        args.meta_json.write_text(json.dumps(meta), encoding="utf-8")
    print(f"Wrote {meta['output_path']}")
    print(
        f"  work_gsd={meta['gsd_work_m']:.4f} m/px  "
        f"therm_gsd={meta['gsd_thermal_m']:.4f} m/px  "
        f"work={meta['work_shape_hw']}  native={meta['native_shape_hw']}"
    )
    print(
        f"  pass1={meta.get('pass1')}  tile_pairs={meta.get('n_tile_pairs')}  "
        f"akaze={meta.get('n_akaze_matches')}  inliers={meta.get('n_inliers')}  "
        f"scale_xy=({meta.get('scale_x', float('nan')):.5f},"
        f"{meta.get('scale_y', float('nan')):.5f})  "
        f"rot={meta.get('rotation_deg', float('nan')):.3f}deg  "
        f"ecc={meta.get('ecc_cc')}"
    )
    print(f"  pass1_only={meta.get('pass1_output_path')}")
    print(
        f"  ctrl_pts={meta['ctrl_pts']}  tile={meta['tile_px']}px  "
        f"runtime={meta['runtime_s']:.1f}s "
        f"(prep={meta['prep_reproject_s']:.1f}s pass1={meta['global_pass1_s']:.1f}s "
        f"tps={meta['tile_tps_s']:.1f}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
