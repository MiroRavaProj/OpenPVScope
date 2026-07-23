"""Detection pipeline: per-modality AOI/grid + deskewed template matching → oriented panels."""

from __future__ import annotations

import json
import math
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.windows import from_bounds, transform as window_transform
from pyproj import Transformer

from openpvscope.detection.deskew import (
    aoi_deskew_angle_deg,
    apply_m,
    invert_m,
    oriented_quads_from_seed,
    warp_rgb,
)
from openpvscope.detection.grid import build_grid_cells, regularize_quad
from openpvscope.detection.template_match import extract_patch_rgb, match_templates
from openpvscope.geo.crs import (
    feature_collection,
    polygon_feature,
    ring_to_lonlat,
    transformer_to_wgs84,
)
from openpvscope.io_atomic import atomic_write_json
from openpvscope.project.paths import ortho_rgb, ortho_thermal_aligned

ProgressCb = Callable[[float | None, str], None]
LogCb = Callable[[str, str], None]  # (level, message) level: info|verbose
Modality = Literal["rgb", "thermal"]

# Thesis pipeline defaults
DEFAULT_CONFIDENCE = 0.5
DEFAULT_NMS_IOU = 0.05
DEFAULT_NUM_TEMPLATES = 1


def detection_dir(root: Path, modality: Modality = "rgb") -> Path:
    if modality not in ("rgb", "thermal"):
        raise ValueError(f"Unknown modality: {modality}")
    det_dir = Path(root) / "detection" / modality
    det_dir.mkdir(parents=True, exist_ok=True)
    return det_dir


def _ortho_for(root: Path, modality: Modality) -> Path:
    if modality == "rgb":
        path = ortho_rgb(root)
        if not path.is_file():
            raise FileNotFoundError("RGB orthophoto required")
        return path
    path = ortho_thermal_aligned(root)
    if not path.is_file():
        raise FileNotFoundError("Aligned thermal orthophoto required — complete ortho alignment first")
    return path


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_aoi_geojson(
    root: Path,
    ring_lonlat: list[list[float]],
    *,
    modality: Modality = "rgb",
    regenerate_grid: bool = False,
) -> Path:
    """ring_lonlat: [[lon,lat], ...] exactly 4 corners."""
    pts = [(float(p[0]), float(p[1])) for p in ring_lonlat[:4]]
    if len(pts) != 4:
        raise ValueError("AOI needs 4 corners")
    rect = regularize_quad(pts)
    det_dir = detection_dir(root, modality)
    feat = polygon_feature([[x, y] for x, y in rect], {"kind": "aoi", "modality": modality}, fid="aoi")
    out = det_dir / "aoi.geojson"
    atomic_write_json(out, feature_collection([feat], name="aoi"))
    atomic_write_json(det_dir / "aoi_ring.json", {"ring": [[x, y] for x, y in rect], "crs": "EPSG:4326"})
    if regenerate_grid:
        meta = _read_json(det_dir / "grid_meta.json") or {}
        rows = int(meta.get("rows") or 0)
        cols = int(meta.get("cols") or 0)
        if rows >= 1 and cols >= 1:
            generate_grid(root, rows=rows, cols=cols, modality=modality)
    return out


def generate_grid(
    root: Path,
    *,
    rows: int,
    cols: int,
    modality: Modality = "rgb",
) -> dict[str, Any]:
    det_dir = detection_dir(root, modality)
    data = _read_json(det_dir / "aoi_ring.json")
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
                {"kind": "grid", "row": cell["row"], "col": cell["col"], "modality": modality},
                fid=fid,
            )
        )
    fc = feature_collection(features, name="grid")
    out = det_dir / "grid.geojson"
    atomic_write_json(out, fc)
    atomic_write_json(det_dir / "grid_meta.json", {"rows": rows, "cols": cols, "cell_count": len(cells)})
    return {"rows": rows, "cols": cols, "cell_count": len(cells), "path": str(out), "modality": modality}


def copy_rgb_grid_to_thermal(root: Path) -> dict[str, Any]:
    """Copy RGB AOI + grid artifacts into detection/thermal/."""
    root = Path(root)
    src = detection_dir(root, "rgb")
    dst = detection_dir(root, "thermal")
    needed = ["aoi.geojson", "aoi_ring.json", "grid.geojson", "grid_meta.json"]
    missing = [n for n in needed if not (src / n).is_file()]
    if missing:
        raise FileNotFoundError(f"RGB grid incomplete — missing {', '.join(missing)}")
    for name in needed:
        shutil.copy2(src / name, dst / name)
    for name in ("aoi.geojson", "grid.geojson"):
        data = _read_json(dst / name)
        if not data:
            continue
        for feat in data.get("features") or []:
            props = feat.setdefault("properties", {})
            props["modality"] = "thermal"
        atomic_write_json(dst / name, data)
    return {"ok": True, "copied": needed}


def _lonlat_to_xy(lon: float, lat: float, transformer_from_wgs84) -> tuple[float, float]:
    if transformer_from_wgs84 is None:
        return lon, lat
    x, y = transformer_from_wgs84.transform(lon, lat)
    return float(x), float(y)


def _to_uint8_rgb(arr: np.ndarray, *, thermal: bool, dtype_in) -> np.ndarray:
    """Match old suite: uint8 RGB kept as-is; else min-max; thermal min-max on valid temps."""
    if thermal or arr.shape[0] == 1:
        band = arr[0].astype(np.float32)
        valid = np.isfinite(band) & (band > -100)
        out = np.zeros(band.shape, dtype=np.uint8)
        if np.any(valid):
            lo = float(np.min(band[valid]))
            hi = float(np.max(band[valid]))
            if hi > lo:
                scaled = (band - lo) / (hi - lo) * 255.0
                out = np.where(valid, np.clip(scaled, 0, 255).astype(np.uint8), 0)
        return np.stack([out, out, out], axis=-1)

    rgb = np.transpose(arr[:3], (1, 2, 0))
    if dtype_in == np.uint8 or str(dtype_in) == "uint8":
        return np.ascontiguousarray(rgb[:, :, :3])
    rgb_f = rgb.astype(np.float32)
    lo = float(np.min(rgb_f))
    hi = float(np.max(rgb_f))
    if hi > lo:
        return np.clip((rgb_f - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    return np.zeros_like(rgb_f, dtype=np.uint8)


def run_detection(
    root: Path,
    *,
    modality: Modality = "rgb",
    confidence: float = DEFAULT_CONFIDENCE,
    nms_iou: float = DEFAULT_NMS_IOU,
    num_templates: int = DEFAULT_NUM_TEMPLATES,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict[str, Any]:
    root = Path(root)
    ortho = _ortho_for(root, modality)
    det_dir = detection_dir(root, modality)

    def prog(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    def vlog(msg: str) -> None:
        if log:
            log("verbose", msg)

    def ilog(msg: str) -> None:
        if log:
            log("info", msg)
        else:
            prog(None, msg)

    prog(5, f"Loading {modality} AOI and grid")
    aoi = _read_json(det_dir / "aoi_ring.json")
    grid_fc = _read_json(det_dir / "grid.geojson")
    if not aoi or not grid_fc:
        raise FileNotFoundError(f"{modality}: AOI and grid required — generate grid first")

    ring_wgs = aoi["ring"]
    angle = aoi_deskew_angle_deg(ring_wgs)
    grid_feats = grid_fc.get("features") or []
    vlog(f"[{modality}] AOI corners={len(ring_wgs)} grid_cells={len(grid_feats)} deskew={angle:.3f}°")
    vlog(f"[{modality}] params confidence={confidence} nms_iou={nms_iou} num_templates={num_templates}")
    prog(12, f"Deskew {angle:.2f}° — reading AOI at native resolution")

    features: list[dict] = []
    out = det_dir / "panels.geojson"

    with rasterio.open(ortho) as ds:
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
        # Pad so rotation does not clip the AOI
        pad_x = (east - west) * 0.12 + abs(ds.transform.a) * 8
        pad_y = (north - south) * 0.12 + abs(ds.transform.e) * 8
        west -= pad_x
        east += pad_x
        south -= pad_y
        north += pad_y

        window = from_bounds(west, south, east, north, transform=ds.transform)
        # Native resolution — do not downsample (old suite matched on full-res raster)
        out_w = max(1, int(round(window.width)))
        out_h = max(1, int(round(window.height)))
        vlog(
            f"[{modality}] ortho={ds.width}x{ds.height} bands={ds.count} dtype={ds.dtypes[0]} "
            f"AOI window={out_w}x{out_h} px"
        )

        if modality == "thermal" or ds.count < 3:
            g = ds.read(
                1,
                window=window,
                out_shape=(out_h, out_w),
                resampling=Resampling.bilinear,
                boundless=True,
                fill_value=np.nan,
            )
            data = np.stack([g], axis=0)
            image = _to_uint8_rgb(data, thermal=True, dtype_in=ds.dtypes[0])
            use_color = False
        else:
            data = ds.read(
                [1, 2, 3],
                window=window,
                out_shape=(3, out_h, out_w),
                resampling=Resampling.bilinear,
                boundless=True,
                fill_value=0,
            )
            image = _to_uint8_rgb(data, thermal=False, dtype_in=ds.dtypes[0])
            use_color = True

        win_transform = window_transform(window, ds.transform)
        pix_transform = win_transform * Affine.scale(window.width / out_w, window.height / out_h)

        def crs_to_pixel(x: float, y: float) -> tuple[float, float]:
            inv = ~pix_transform
            c, r = inv * (x, y)
            return float(c), float(r)

        def pixel_to_crs(c: float, r: float) -> tuple[float, float]:
            return pix_transform * (c, r)

        prog(28, "Warping AOI window (deskew)")
        rotated, m_rot = warp_rgb(image, angle)
        m_inv = invert_m(m_rot)
        vlog(f"[{modality}] search image after deskew: {rotated.shape[1]}x{rotated.shape[0]}")

        prog(40, "Building templates from grid cells")
        templates: list[np.ndarray] = []
        n_tpl = max(1, min(int(num_templates), len(grid_feats) or 1))
        for feat in grid_feats[:n_tpl]:
            coords = feat["geometry"]["coordinates"][0]
            corners_rot = []
            for p in coords[:4]:
                x, y = _lonlat_to_xy(float(p[0]), float(p[1]), from_wgs)
                col, row = crs_to_pixel(x, y)
                rx, ry = apply_m(m_rot, col, row)
                corners_rot.append((rx, ry))
            xs_t = [c[0] for c in corners_rot]
            ys_t = [c[1] for c in corners_rot]
            patch = extract_patch_rgb(
                rotated,
                int(math.floor(min(xs_t))),
                int(math.floor(min(ys_t))),
                int(math.ceil(max(xs_t))),
                int(math.ceil(max(ys_t))),
            )
            if patch is not None and patch.shape[0] >= 4 and patch.shape[1] >= 4:
                templates.append(patch)
                vlog(f"[{modality}] template[{len(templates)-1}] size={patch.shape[1]}x{patch.shape[0]}")
        if not templates:
            raise RuntimeError(f"Could not extract a template from the {modality} grid")

        prog(55, f"Template matching ({len(templates)} tpl, color={use_color})")
        all_dets, raw_peaks = match_templates(
            rotated,
            templates,
            threshold=confidence,
            nms_iou=nms_iou,
            use_color=use_color,
        )
        vlog(f"[{modality}] raw peaks above threshold: {raw_peaks}")
        vlog(f"[{modality}] detections after NMS: {len(all_dets)}")
        ilog(f"[{modality}] {len(all_dets)} panels after NMS (from {raw_peaks} peaks)")

        seed_feat = grid_feats[0] if grid_feats else None
        if not seed_feat:
            raise RuntimeError("Grid has no cells")
        seed_ring = [[float(p[0]), float(p[1])] for p in seed_feat["geometry"]["coordinates"][0][:4]]

        centers: list[tuple[float, float]] = []
        confidences: list[float] = []
        for det in all_dets:
            x, y, w, h = det["bbox"]
            cx_r, cy_r = x + w / 2.0, y + h / 2.0
            col, row = apply_m(m_inv, cx_r, cy_r)
            x_crs, y_crs = pixel_to_crs(col, row)
            lonlat = ring_to_lonlat([[x_crs, y_crs]], to_wgs)[0]
            centers.append((float(lonlat[0]), float(lonlat[1])))
            confidences.append(float(det["confidence"]))

        prog(85, f"Writing {len(centers)} oriented panels")
        quads = oriented_quads_from_seed(seed_ring, centers)
        for ring_ll, conf in zip(quads, confidences):
            pid = uuid.uuid4().hex[:12]
            features.append(
                polygon_feature(
                    ring_ll,
                    {
                        "kind": "panel",
                        "confidence": conf,
                        "id": pid,
                        "modality": modality,
                    },
                    fid=pid,
                )
            )

        fc = feature_collection(features, name="panels")
        atomic_write_json(out, fc)
        atomic_write_json(
            det_dir / "detection_meta.json",
            {
                "count": len(features),
                "confidence": confidence,
                "nms_iou": nms_iou,
                "templates_used": len(templates),
                "deskew_angle_deg": angle,
                "modality": modality,
                "search_size": [int(rotated.shape[1]), int(rotated.shape[0])],
                "window_size": [out_w, out_h],
            },
        )
        vlog(f"[{modality}] wrote {out}")

    prog(100, f"{modality} detection complete — {len(features)} panels")
    return {"count": len(features), "path": str(out), "modality": modality}


def _modality_status(root: Path, modality: Modality) -> dict[str, Any]:
    det_dir = detection_dir(root, modality)
    panels = det_dir / "panels.geojson"
    aoi = det_dir / "aoi.geojson"
    grid = det_dir / "grid.geojson"
    count = 0
    if panels.is_file():
        try:
            count = len(json.loads(panels.read_text(encoding="utf-8")).get("features") or [])
        except Exception:
            count = 0
    return {
        "has_aoi": aoi.is_file(),
        "has_grid": grid.is_file(),
        "has_panels": panels.is_file() and count > 0,
        "panel_count": count,
    }


def detection_status(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    rgb = _modality_status(root, "rgb")
    th = _modality_status(root, "thermal")
    total = rgb["panel_count"] + th["panel_count"]
    ready = rgb["has_panels"] or th["has_panels"]
    parts = []
    if rgb["has_panels"]:
        parts.append(f"RGB {rgb['panel_count']}")
    if th["has_panels"]:
        parts.append(f"Thermal {th['panel_count']}")
    message = (
        f"Panels: {', '.join(parts)}"
        if parts
        else "Draw AOI on RGB, generate grid, copy to thermal, then run detection on both"
    )
    return {
        "ready": ready,
        "message": message,
        "has_aoi": rgb["has_aoi"],
        "has_grid": rgb["has_grid"],
        "has_rgb_panels": rgb["has_panels"],
        "has_thermal_panels": th["has_panels"],
        "panel_count": total,
        "rgb": rgb,
        "thermal": th,
        "both_grids_ready": bool(rgb["has_grid"] and th["has_grid"]),
    }


def load_geojson(root: Path, name: str, modality: Modality = "rgb") -> dict | None:
    path = detection_dir(root, modality) / f"{name}.geojson"
    return _read_json(path)


def clear_detection(root: Path, modality: Modality | None = None) -> None:
    mods: list[Modality] = ["rgb", "thermal"] if modality is None else [modality]
    for mod in mods:
        det_dir = detection_dir(root, mod)
        for name in (
            "aoi.geojson",
            "aoi_ring.json",
            "grid.geojson",
            "grid_meta.json",
            "panels.geojson",
            "detection_meta.json",
        ):
            p = det_dir / name
            if p.is_file():
                p.unlink()
