"""Detection pipeline: full-ortho deskew + multi-template match (legacy suite behavior)."""

from __future__ import annotations

import json
import math
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import rasterio
from pyproj import Transformer

from openpvscope.detection.deskew import (
    aoi_deskew_angle_deg,
    apply_m,
    invert_m,
    oriented_quads_from_seed,
    warp_image,
)
from openpvscope.detection.grid import build_grid_cells, regularize_quad
from openpvscope.detection.refine import run_advanced_validation
from openpvscope.detection.template_match import _Heartbeat, extract_patch, match_templates
from openpvscope.geo.crs import (
    feature_collection,
    polygon_feature,
    ring_to_lonlat,
    transformer_to_wgs84,
)
from openpvscope.io_atomic import atomic_write_json
from openpvscope.project.paths import ortho_rgb, ortho_thermal, ortho_thermal_aligned

ProgressCb = Callable[[float | None, str], None]
# Extended: optional level kw via a thin wrapper in jobs — pipeline uses prog/vlog
LogCb = Callable[[str, str], None]
Modality = Literal["rgb", "thermal"]

# Legacy suite defaults (template_matching_threshold / nms / display filter)
DEFAULT_CONFIDENCE = 0.5
DEFAULT_NMS_IOU = 0.05
DEFAULT_NUM_TEMPLATES = 0  # 0 => all grid cells
DEFAULT_THERMAL_TEMP_CAP = 45.0  # °C
DEFAULT_DISPLAY_CONFIDENCE = 0.7  # map visualization filter only
PIPELINE_REV = "detect-v11"


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
    aligned = ortho_thermal_aligned(root)
    if aligned.is_file():
        return aligned
    raw = ortho_thermal(root)
    if raw.is_file():
        # Thermal-only (or pre-alignment) projects use the raw thermal ortho
        return raw
    raise FileNotFoundError(
        "Thermal orthophoto required — run photogrammetry (or complete alignment for RGB+thermal)"
    )


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


def _load_rgb_uint8(ds: rasterio.DatasetReader) -> np.ndarray:
    """HxWx3 RGB uint8 — keep uint8 as-is, else min-max."""
    data = ds.read([1, 2, 3])
    rgb = np.transpose(data[:3], (1, 2, 0))
    if ds.dtypes[0] == "uint8" or np.dtype(ds.dtypes[0]) == np.uint8:
        return np.ascontiguousarray(rgb[:, :, :3])
    rgb_f = rgb.astype(np.float32)
    lo = float(np.min(rgb_f))
    hi = float(np.max(rgb_f))
    if hi > lo:
        return np.clip((rgb_f - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    return np.zeros_like(rgb_f, dtype=np.uint8)


def _load_thermal_uint8(
    ds: rasterio.DatasetReader,
    *,
    thermal_temp_cap: float | None,
) -> np.ndarray:
    """
    HxW uint8 — IR path: mask < -100°C, optional cap, min-max valid → uint8.
    """
    band = ds.read(1).astype(np.float32)
    nodata_mask = ~np.isfinite(band) | (band < -100)
    capped = band.copy()
    if thermal_temp_cap is not None:
        high = capped > float(thermal_temp_cap)
        capped[high & ~nodata_mask] = float(thermal_temp_cap)
    valid = ~nodata_mask
    out = np.zeros(band.shape, dtype=np.uint8)
    if np.any(valid):
        lo = float(np.min(capped[valid]))
        hi = float(np.max(capped[valid]))
        if hi > lo:
            scaled = (capped[valid] - lo) / (hi - lo) * 255.0
            out[valid] = np.clip(scaled, 0, 255).astype(np.uint8)
    return out


def run_detection(
    root: Path,
    *,
    modality: Modality = "rgb",
    confidence: float = DEFAULT_CONFIDENCE,
    nms_iou: float = DEFAULT_NMS_IOU,
    num_templates: int = DEFAULT_NUM_TEMPLATES,
    thermal_temp_cap: float | None = DEFAULT_THERMAL_TEMP_CAP,
    advanced_validation: bool = True,
    fine_tuning_confidence: float = 0.65,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict[str, Any]:
    root = Path(root)
    ortho = _ortho_for(root, modality)
    det_dir = detection_dir(root, modality)

    def prog(p: float | None, msg: str, *, level: str = "info") -> None:
        if progress:
            # jobs wrapper accepts (p, msg); level forwarded via attribute if present
            try:
                progress(p, msg, level=level)  # type: ignore[call-arg]
            except TypeError:
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
    grid_feats = grid_fc.get("features") or []
    if not grid_feats:
        raise RuntimeError("Grid has no cells")

    features: list[dict] = []
    out = det_dir / "panels.geojson"
    refine_stats: dict[str, Any] | None = None

    with rasterio.open(ortho) as ds:
        to_wgs = transformer_to_wgs84(ds.crs)
        from_wgs = None
        if to_wgs is not None:
            from_wgs = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)

        original_affine = ds.transform
        # Pixel-space longest side (legacy suite)
        angle = aoi_deskew_angle_deg(ring_wgs, affine=original_affine)
        vlog(f"[{modality}] AOI corners=4 grid_cells={len(grid_feats)} deskew={angle:.3f}° (pixel-space)")
        vlog(
            f"[{modality}] params confidence={confidence} nms_iou={nms_iou} "
            f"num_templates={'ALL' if num_templates <= 0 else num_templates} "
            f"thermal_cap={thermal_temp_cap}"
        )
        prog(12, f"Deskew {angle:.2f}° — reading FULL orthomosaic")

        out_w, out_h = int(ds.width), int(ds.height)
        vlog(f"[{modality}] ortho={out_w}x{out_h} bands={ds.count} dtype={ds.dtypes[0]} search=FULL_ORTHO")

        load_label = f"[{modality}] reading FULL orthomosaic ({out_w}x{out_h})"
        with _Heartbeat(
            (lambda p, m: prog(p, m, level="verbose")) if progress else None,
            18.0,
            load_label,
            interval_s=2.0,
        ):
            if modality == "thermal" or ds.count < 3:
                image = _load_thermal_uint8(ds, thermal_temp_cap=thermal_temp_cap)
                use_color = False
            else:
                image = _load_rgb_uint8(ds)
                use_color = True
        vlog(f"[{modality}] ortho loaded shape={image.shape} color={use_color}")

        def crs_to_pixel(x: float, y: float) -> tuple[float, float]:
            inv = ~original_affine
            c, r = inv * (x, y)
            return float(c), float(r)

        def pixel_to_crs(c: float, r: float) -> tuple[float, float]:
            return original_affine * (c, r)

        prog(28, "Warping FULL orthomosaic (deskew)")
        warp_label = f"[{modality}] warpAffine deskew {angle:.2f}°"
        with _Heartbeat(
            (lambda p, m: prog(p, m, level="verbose")) if progress else None,
            32.0,
            warp_label,
            interval_s=2.0,
        ):
            rotated, m_rot = warp_image(image, angle)
        m_inv = invert_m(m_rot)
        vlog(f"[{modality}] search after deskew: {rotated.shape[1]}x{rotated.shape[0]} ndim={rotated.ndim}")
        del image

        prog(40, "Building templates from ALL grid cells" if num_templates <= 0 else "Building templates from grid")
        if num_templates <= 0:
            feats_for_tpl = grid_feats
        else:
            feats_for_tpl = grid_feats[: max(1, min(int(num_templates), len(grid_feats)))]

        templates: list[np.ndarray] = []
        n_feats = len(feats_for_tpl)
        for fi, feat in enumerate(feats_for_tpl):
            if fi % 5 == 0 or fi + 1 == n_feats:
                prog(
                    40.0 + (fi / max(n_feats, 1)) * 14.0,
                    f"extract template {fi + 1}/{n_feats}",
                    level="verbose",
                )
                vlog(f"[{modality}] extract template {fi + 1}/{n_feats}")
            coords = feat["geometry"]["coordinates"][0]
            corners_rot = []
            for p in coords[:4]:
                x, y = _lonlat_to_xy(float(p[0]), float(p[1]), from_wgs)
                col, row = crs_to_pixel(x, y)
                rx, ry = apply_m(m_rot, col, row)
                corners_rot.append((rx, ry))
            xs_t = [c[0] for c in corners_rot]
            ys_t = [c[1] for c in corners_rot]
            patch = extract_patch(
                rotated,
                int(math.floor(min(xs_t))),
                int(math.floor(min(ys_t))),
                int(math.ceil(max(xs_t))),
                int(math.ceil(max(ys_t))),
            )
            if patch is not None and patch.shape[0] >= 4 and patch.shape[1] >= 4:
                templates.append(patch)
        if not templates:
            raise RuntimeError(f"Could not extract a template from the {modality} grid")
        vlog(f"[{modality}] templates={len(templates)} (from {len(feats_for_tpl)} cells)")
        ilog(f"[{modality}] using {len(templates)} templates")

        prog(55, f"Multi-template matching ({len(templates)} tpl, color={use_color})")

        def match_prog(local_pct: float, msg: str) -> None:
            # Map matching phase into 55–82% of this modality's progress
            mapped = 55.0 + (local_pct / 100.0) * 27.0
            prog(mapped, msg, level="verbose")
            vlog(f"[{modality}] {msg}")

        all_dets, raw_peaks = match_templates(
            rotated,
            templates,
            threshold=confidence,
            nms_iou=nms_iou,
            use_color=use_color,
            progress=match_prog,
        )
        prog(82, f"{len(all_dets)} panels after NMS (from {raw_peaks} peaks)")
        vlog(f"[{modality}] raw peaks: {raw_peaks} → after NMS: {len(all_dets)}")
        ilog(f"[{modality}] {len(all_dets)} panels after NMS (from {raw_peaks} peaks)")

        refine_stats = None
        if advanced_validation and all_dets:
            tw = float(np.mean([t.shape[1] for t in templates]))
            th = float(np.mean([t.shape[0] for t in templates]))

            def refine_prog(_p: float | None, msg: str) -> None:
                prog(83, msg)
                vlog(f"[{modality}] {msg}")

            all_dets, refine_stats = run_advanced_validation(
                all_dets,
                tw,
                th,
                fine_tuning_confidence_threshold=fine_tuning_confidence,
                progress=refine_prog,
            )
            ilog(
                f"[{modality}] refine: {refine_stats['input']} → {refine_stats['after_step3']} "
                f"(filled +{refine_stats['filled']})"
            )

        seed_ring = [[float(p[0]), float(p[1])] for p in grid_feats[0]["geometry"]["coordinates"][0][:4]]
        centers: list[tuple[float, float]] = []
        det_meta: list[dict[str, Any]] = []
        for det in all_dets:
            x, y, w, h = det["bbox"]
            cx_r, cy_r = x + w / 2.0, y + h / 2.0
            col, row = apply_m(m_inv, cx_r, cy_r)
            x_crs, y_crs = pixel_to_crs(col, row)
            lonlat = ring_to_lonlat([[x_crs, y_crs]], to_wgs)[0]
            centers.append((float(lonlat[0]), float(lonlat[1])))
            det_meta.append(
                {
                    "confidence": float(det.get("confidence") or 0.0),
                    "bbox_w": float(w),
                    "bbox_h": float(h),
                    "bbox_pixels": [float(x), float(y), float(w), float(h)],
                    "deskew_angle_deg": float(angle),
                    "cluster_id": det.get("cluster_id"),
                    "is_grid_aligned": bool(det.get("is_grid_aligned", True)),
                    "border_outlier": bool(det.get("border_outlier", False)),
                    "filled_panel": bool(det.get("filled_panel", False)),
                    "restored_panel": bool(det.get("restored_panel", False)),
                }
            )

        prog(88, f"Writing {len(centers)} oriented panels [{PIPELINE_REV}]")
        quads = oriented_quads_from_seed(seed_ring, centers)
        for ring_ll, meta in zip(quads, det_meta):
            pid = uuid.uuid4().hex[:12]
            features.append(
                polygon_feature(
                    ring_ll,
                    {
                        "kind": "panel",
                        "confidence": meta["confidence"],
                        "id": pid,
                        "modality": modality,
                        "bbox_w": meta["bbox_w"],
                        "bbox_h": meta["bbox_h"],
                        "deskew_angle_deg": meta["deskew_angle_deg"],
                        "cluster_id": meta["cluster_id"],
                        "is_grid_aligned": meta["is_grid_aligned"],
                        "filled_panel": meta["filled_panel"],
                        "restored_panel": meta["restored_panel"],
                    },
                    fid=pid,
                )
            )

        atomic_write_json(out, feature_collection(features, name="panels"))
        atomic_write_json(
            det_dir / "detection_meta.json",
            {
                "count": len(features),
                "confidence": confidence,
                "nms_iou": nms_iou,
                "templates_used": len(templates),
                "num_templates_request": num_templates,
                "deskew_angle_deg": angle,
                "modality": modality,
                "search_mode": "full_ortho",
                "thermal_temp_cap": thermal_temp_cap if modality == "thermal" else None,
                "search_size": [int(rotated.shape[1]), int(rotated.shape[0])],
                "ortho_size": [out_w, out_h],
                "pipeline_rev": PIPELINE_REV,
                "advanced_validation": advanced_validation,
                "fine_tuning_confidence": fine_tuning_confidence,
                "refine_stats": refine_stats,
            },
        )
        vlog(f"[{modality}] wrote {out}")

    prog(100, f"{modality} detection complete — {len(features)} panels [{PIPELINE_REV}]")
    return {
        "count": len(features),
        "path": str(out),
        "modality": modality,
        "pipeline_rev": PIPELINE_REV,
        "refine_stats": refine_stats if advanced_validation else None,
    }


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

    thermal_only = False
    try:
        from openpvscope.photogrammetry.setup import load_setup

        thermal_only = load_setup(root).get("modalities") == "thermal_only"
    except Exception:
        thermal_only = False

    if parts:
        message = f"Panels: {', '.join(parts)}"
    elif thermal_only:
        if th["has_grid"]:
            message = "Thermal grid ready — run thermal detection"
        elif th["has_aoi"]:
            message = "Thermal AOI ready — generate grid, then run detection"
        else:
            message = "Draw AOI on thermal, generate grid, then run detection"
    else:
        message = "Draw AOI on RGB, generate grid, copy to thermal, then run detection on both"

    primary = th if thermal_only else rgb
    return {
        "ready": ready,
        "message": message,
        "has_aoi": primary["has_aoi"],
        "has_grid": primary["has_grid"],
        "has_rgb_panels": rgb["has_panels"],
        "has_thermal_panels": th["has_panels"],
        "panel_count": total,
        "rgb": rgb,
        "thermal": th,
        "both_grids_ready": (
            bool(th["has_grid"]) if thermal_only else bool(rgb["has_grid"] and th["has_grid"])
        ),
        "defaults": {
            "confidence": DEFAULT_CONFIDENCE,
            "nms_iou": DEFAULT_NMS_IOU,
            "num_templates": DEFAULT_NUM_TEMPLATES,
            "thermal_temp_cap": DEFAULT_THERMAL_TEMP_CAP,
            "display_confidence": DEFAULT_DISPLAY_CONFIDENCE,
        },
    }


def load_geojson(root: Path, name: str, modality: Modality = "rgb") -> dict | None:
    return _read_json(detection_dir(root, modality) / f"{name}.geojson")


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
