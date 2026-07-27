"""Remove isolated panels using the user-defined AOI grid pitch."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from scipy.spatial import cKDTree

from openpvscope.detection import load_geojson
from openpvscope.detection.panel_selection import apply_panel_selection, detection_dir
from openpvscope.geo.crs import feature_collection
from openpvscope.io_atomic import atomic_write_json
from openpvscope.segmentation.extract import segmentation_root
from openpvscope.segmentation.pairing import _ring_centroid
from openpvscope.segmentation.prefs import load_segmentation_params
from openpvscope.segmentation.preview import build_pair_preview_geojson

Modality = Literal["rgb", "thermal"]
ProgressCb = Callable[..., None]
LogCb = Callable[[str, str], None]

# User-requested: 10% of cell length (cols) and 10% of cell height (rows).
GRID_TOL_FRAC = 0.10


def _emit(
    progress: ProgressCb | None,
    log: LogCb | None,
    p: float | None,
    msg: str,
    *,
    level: str = "info",
) -> None:
    if progress is not None:
        try:
            progress(p, msg, level=level)
        except TypeError:
            progress(p, msg)
    elif log is not None:
        log(level, msg)


def _feature_center(feat: dict[str, Any]) -> tuple[str, float, float] | None:
    props = feat.get("properties") or {}
    pid = str(props.get("id") or feat.get("id") or "")
    if not pid:
        return None
    geom = feat.get("geometry") or {}
    if geom.get("type") != "Polygon":
        return None
    ring = (geom.get("coordinates") or [[]])[0]
    if not ring:
        return None
    lon, lat = _ring_centroid(ring)
    return pid, lon, lat


def _deg_to_m(len_deg: float, lat_deg: float) -> float:
    """Rough degrees → meters at latitude (for console diagnostics only)."""
    return float(len_deg * 111_320.0 * max(0.2, abs(math.cos(math.radians(lat_deg)))))


def load_user_grid_pitch(
    root: Path,
    modality: Modality,
) -> dict[str, Any]:
    """
    Pitch from user grid cell centers (grid.geojson), fallback AOI÷rows/cols.

    - pitch_e: median center delta between (row, col) → (row, col+1)  [length]
    - pitch_f: median center delta between (row, col) → (row+1, col)  [height]
    """
    det = detection_dir(root, modality)
    meta_path = det / "grid_meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"No grid for {modality} — generate the rows×cols grid first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows = int(meta.get("rows") or 0)
    cols = int(meta.get("cols") or 0)
    if rows < 1 or cols < 1:
        raise FileNotFoundError(f"Invalid grid rows/cols for {modality}")

    grid_path = det / "grid.geojson"
    if grid_path.is_file():
        fc = json.loads(grid_path.read_text(encoding="utf-8"))
        centers: dict[tuple[int, int], np.ndarray] = {}
        for feat in fc.get("features") or []:
            props = feat.get("properties") or {}
            try:
                r = int(props["row"])
                c = int(props["col"])
            except (KeyError, TypeError, ValueError):
                continue
            cen = _feature_center(feat)
            if cen is None:
                continue
            centers[(r, c)] = np.array([cen[1], cen[2]], dtype=np.float64)

        e_vecs: list[np.ndarray] = []
        f_vecs: list[np.ndarray] = []
        for (r, c), xy in centers.items():
            right = centers.get((r, c + 1))
            if right is not None:
                e_vecs.append(right - xy)
            below = centers.get((r + 1, c))
            if below is not None:
                f_vecs.append(below - xy)

        if e_vecs and f_vecs:
            pitch_e = np.median(np.asarray(e_vecs, dtype=np.float64), axis=0)
            pitch_f = np.median(np.asarray(f_vecs, dtype=np.float64), axis=0)
            origin = centers[min(centers.keys(), key=lambda rc: (rc[0], rc[1]))]
            len_e = float(np.hypot(pitch_e[0], pitch_e[1]))
            len_f = float(np.hypot(pitch_f[0], pitch_f[1]))
            if len_e > 1e-15 and len_f > 1e-15:
                return {
                    "modality": modality,
                    "rows": rows,
                    "cols": cols,
                    "origin": origin,
                    "pitch_e": pitch_e,
                    "pitch_f": pitch_f,
                    "length_deg": len_e,
                    "height_deg": len_f,
                    "basis": np.column_stack([pitch_e, pitch_f]),
                    "source": "grid.geojson cell centers",
                    "n_pitch_e_samples": len(e_vecs),
                    "n_pitch_f_samples": len(f_vecs),
                }

    # Fallback: AOI ring ÷ rows/cols
    aoi_path = det / "aoi_ring.json"
    if not aoi_path.is_file():
        raise FileNotFoundError(f"No AOI for {modality} — draw AOI / generate grid first")
    aoi = json.loads(aoi_path.read_text(encoding="utf-8"))
    ring = aoi.get("ring") or []
    if len(ring) < 4:
        raise FileNotFoundError(f"AOI ring incomplete for {modality}")
    p0 = np.array([float(ring[0][0]), float(ring[0][1])], dtype=np.float64)
    p1 = np.array([float(ring[1][0]), float(ring[1][1])], dtype=np.float64)
    p3 = np.array([float(ring[3][0]), float(ring[3][1])], dtype=np.float64)
    pitch_e = (p1 - p0) / float(cols)
    pitch_f = (p3 - p0) / float(rows)
    len_e = float(np.hypot(pitch_e[0], pitch_e[1]))
    len_f = float(np.hypot(pitch_f[0], pitch_f[1]))
    if len_e < 1e-15 or len_f < 1e-15:
        raise ValueError(f"Degenerate grid pitch for {modality}")
    return {
        "modality": modality,
        "rows": rows,
        "cols": cols,
        "origin": p0,
        "pitch_e": pitch_e,
        "pitch_f": pitch_f,
        "length_deg": len_e,
        "height_deg": len_f,
        "basis": np.column_stack([pitch_e, pitch_f]),
        "source": "aoi_ring ÷ rows/cols",
        "n_pitch_e_samples": 1,
        "n_pitch_f_samples": 1,
    }


def centers_to_grid_uv(
    lons: np.ndarray,
    lats: np.ndarray,
    grid: dict[str, Any],
) -> np.ndarray:
    """Project lon/lat centers into grid cell units (u along cols, v along rows)."""
    origin = grid["origin"]
    basis = grid["basis"]
    rel = np.column_stack([lons, lats]).astype(np.float64) - origin
    try:
        uv = np.linalg.solve(basis, rel.T).T
    except np.linalg.LinAlgError:
        uv, *_ = np.linalg.lstsq(basis, rel.T, rcond=None)
        uv = np.asarray(uv.T, dtype=np.float64)
    return np.asarray(uv, dtype=np.float64)


def find_isolated_panel_ids(
    fc: dict[str, Any] | None,
    grid: dict[str, Any],
    *,
    tol_frac: float = GRID_TOL_FRAC,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
    progress_lo: float = 0.0,
    progress_hi: float = 100.0,
    label: str = "panels",
) -> list[str]:
    """
    Panels with zero neighbors at exactly ±1 grid pitch (L/R/U/D).

    A neighbor counts only if offset from self is within:
      |Δu − (±1)| ≤ tol_frac  and  |Δv| ≤ tol_frac   (horizontal)
      |Δv − (±1)| ≤ tol_frac  and  |Δu| ≤ tol_frac   (vertical)
    """
    span = max(0.0, progress_hi - progress_lo)

    def pmap(t: float) -> float:
        return progress_lo + span * t

    if not fc:
        return []
    feats = fc.get("features") or []
    lat0 = float(grid["origin"][1])
    len_m = _deg_to_m(float(grid["length_deg"]), lat0)
    h_m = _deg_to_m(float(grid["height_deg"]), lat0)
    _emit(
        progress,
        log,
        pmap(0.05),
        f"[{label}] Pitch from {grid.get('source')} | "
        f"{grid['rows']}×{grid['cols']} | "
        f"length≈{len_m:.2f} m height≈{h_m:.2f} m | tol={tol_frac:.0%} | "
        f"n={len(feats)}",
        level="info",
    )

    items: list[tuple[str, float, float]] = []
    for feat in feats:
        c = _feature_center(feat)
        if c is not None:
            items.append(c)
    if len(items) < 2:
        ids = [pid for pid, _, _ in items]
        _emit(
            progress,
            log,
            pmap(1.0),
            f"[{label}] <2 panels — treating {len(ids)} as isolated",
            level="info",
        )
        return ids

    ids = [p[0] for p in items]
    lons = np.array([p[1] for p in items], dtype=np.float64)
    lats = np.array([p[2] for p in items], dtype=np.float64)
    uv = centers_to_grid_uv(lons, lats, grid)
    n = len(ids)
    tree = cKDTree(uv)
    tol = float(tol_frac)

    # Diagnostic: median NN in cell units — should be ~1 if pitch matches array.
    nn_d, _ = tree.query(uv, k=2, p=2)
    med_nn = float(np.median(nn_d[:, 1])) if n >= 2 else float("nan")
    _emit(
        progress,
        log,
        pmap(0.20),
        f"[{label}] Median nearest-neighbor spacing = {med_nn:.3f} cell units "
        f"(expect ~1.0 if pitch matches the array)",
        level="verbose",
    )
    if med_nn < 0.5 or med_nn > 1.75:
        _emit(
            progress,
            log,
            pmap(0.22),
            f"[{label}] WARNING: median NN {med_nn:.3f} cells looks off — "
            f"check that the {grid['rows']}×{grid['cols']} grid matches panel size",
            level="warn",
        )

    neighbor_count = np.zeros(n, dtype=np.int32)
    directions = (
        ("right (+cols)", 1.0, 0.0),
        ("left (-cols)", -1.0, 0.0),
        ("up (+rows)", 0.0, 1.0),
        ("down (-rows)", 0.0, -1.0),
    )
    self_idx = np.arange(n)
    # Search radius in Chebyshev UV: must cover the 1-pitch target box.
    search_r = 1.0 + tol + 1e-9

    for di, (dname, du_t, dv_t) in enumerate(directions):
        preds = uv + np.array([du_t, dv_t], dtype=np.float64)
        dists, idxs = tree.query(preds, k=1, p=np.inf, distance_upper_bound=search_r)
        valid = np.isfinite(dists) & (idxs >= 0) & (idxs < n) & (idxs != self_idx)
        du = np.full(n, np.nan, dtype=np.float64)
        dv = np.full(n, np.nan, dtype=np.float64)
        hit_idx = idxs[valid].astype(np.int64)
        src_idx = self_idx[valid]
        du[valid] = uv[hit_idx, 0] - uv[src_idx, 0]
        dv[valid] = uv[hit_idx, 1] - uv[src_idx, 1]
        # Strict 1-pitch box in the correct axis (10% length / 10% height).
        ok = (
            valid
            & np.isfinite(du)
            & np.isfinite(dv)
            & (np.abs(du - du_t) <= tol)
            & (np.abs(dv - dv_t) <= tol)
        )
        neighbor_count += ok.astype(np.int32)
        _emit(
            progress,
            log,
            pmap(0.30 + 0.55 * (di + 1) / 4),
            f"[{label}] {dname}: {int(ok.sum())} true 1-pitch hits | "
            f"degree≥1: {int((neighbor_count > 0).sum())}/{n}",
            level="verbose",
        )

    isolated = [ids[i] for i in range(n) if neighbor_count[i] == 0]
    deg_hist = {
        int(d): int(np.count_nonzero(neighbor_count == d)) for d in range(0, 5)
    }
    _emit(
        progress,
        log,
        pmap(1.0),
        f"[{label}] Isolated (0 cardinal 1-pitch neighbors): {len(isolated)} / {n} | "
        f"degree hist {deg_hist}",
        level="info",
    )
    if log and isolated:
        sample = isolated[:12]
        more = f" … +{len(isolated) - len(sample)} more" if len(isolated) > len(sample) else ""
        log("verbose", f"[{label}] Isolated ids (sample): {', '.join(sample)}{more}")
    return isolated


def _prune_extracted_pairs(
    root: Path,
    drop_pair_ids: set[str],
    drop_rgb: set[str],
    drop_th: set[str],
    *,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> int:
    seg = segmentation_root(root)
    pairs_path = seg / "pairs.json"
    gj_path = seg / "pairs.geojson"
    if not pairs_path.is_file():
        _emit(progress, log, None, "No pairs.json — nothing to prune", level="verbose")
        return 0
    try:
        data = json.loads(pairs_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    pairs = list(data.get("pairs") or [])
    _emit(progress, log, None, f"Pruning extracted pairs ({len(pairs)})…", level="info")
    kept: list[dict[str, Any]] = []
    removed = 0
    for p in pairs:
        pid = str(p.get("id") or "")
        rgb_id = str(p.get("rgb_id") or "")
        th_id = str(p.get("thermal_id") or "")
        if (
            (pid and pid in drop_pair_ids)
            or (rgb_id and rgb_id in drop_rgb)
            or (th_id and th_id in drop_th)
        ):
            removed += 1
            dest = seg / "panels" / "".join(c for c in pid if c.isalnum() or c in "-_")
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            continue
        kept.append(p)
    data["pairs"] = kept
    data["count"] = len(kept)
    atomic_write_json(pairs_path, data)

    if gj_path.is_file():
        try:
            gj = json.loads(gj_path.read_text(encoding="utf-8"))
        except Exception:
            gj = None
        if gj:
            feats = [
                f
                for f in (gj.get("features") or [])
                if not (
                    (str((f.get("properties") or {}).get("id") or f.get("id") or "") in drop_pair_ids)
                    or (str((f.get("properties") or {}).get("rgb_id") or "") in drop_rgb)
                    or (str((f.get("properties") or {}).get("thermal_id") or "") in drop_th)
                )
            ]
            atomic_write_json(gj_path, feature_collection(feats, name="pairs"))
    _emit(progress, log, None, f"Pruned {removed} extracted pairs", level="info")
    return removed


def _load_view_feature_collection(
    root: Path,
    *,
    thermal_only: bool,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Features currently relevant to the segmentation map view.

    Uses IoU-filtered pair preview (saved min_iou) — same boxes the slider shows —
    not the full detection cloud (hidden detections caused false neighbors across gaps).
    """
    if thermal_only:
        fc = load_geojson(root, "panels", modality="thermal") or {
            "type": "FeatureCollection",
            "features": [],
        }
        return fc, "thermal panels"

    params = load_segmentation_params(root)
    min_iou = float(params.get("min_iou") or 0.75)
    _emit(
        progress,
        log,
        4,
        f"Building pair preview at min_iou={min_iou:.2f} (matches map IoU filter)…",
        level="info",
    )
    preview = build_pair_preview_geojson(root, min_iou=min_iou)
    return preview, f"pair preview (min_iou={min_iou:.2f})"


def remove_isolated_panels(
    project_root: Path,
    *,
    thermal_only: bool = False,
    tol_frac: float = GRID_TOL_FRAC,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict[str, Any]:
    """
    Exclude panels/pairs with no neighbor at ±1 user-grid pitch (10% L/H tol).

    Runs on the segmentation view set (pairs / preview), not the full detection
    cloud — so a large gap with no visible box is treated as no neighbor.
    """
    root = Path(project_root)
    rgb_drop: list[str] = []
    th_drop: list[str] = []
    pair_drop: list[str] = []

    _emit(
        progress,
        log,
        1,
        f"Remove isolated (strict 1-pitch box, tol={tol_frac:.0%}, thermal_only={thermal_only})",
        level="info",
    )

    grid_mod: Modality = "thermal" if thermal_only else "rgb"
    grid = load_user_grid_pitch(root, grid_mod)
    _emit(
        progress,
        log,
        3,
        f"Grid {grid['rows']}×{grid['cols']} | source={grid.get('source')}",
        level="info",
    )

    view_fc, view_label = _load_view_feature_collection(
        root, thermal_only=thermal_only, progress=progress, log=log
    )
    _emit(
        progress,
        log,
        8,
        f"Isolation set: {view_label} ({len(view_fc.get('features') or [])} boxes)",
        level="info",
    )

    isolated_ids = find_isolated_panel_ids(
        view_fc,
        grid,
        tol_frac=tol_frac,
        progress=progress,
        log=log,
        progress_lo=10,
        progress_hi=70,
        label=view_label,
    )

    if not isolated_ids:
        _emit(progress, log, 90, "No isolated panels in the current view", level="info")
    else:
        # Map view feature ids → detection rgb/thermal ids when present.
        id_set = set(isolated_ids)
        for feat in view_fc.get("features") or []:
            props = feat.get("properties") or {}
            fid = str(props.get("id") or feat.get("id") or "")
            if fid not in id_set:
                continue
            rgb_id = str(props.get("rgb_id") or "")
            th_id = str(props.get("thermal_id") or "")
            if rgb_id:
                rgb_drop.append(rgb_id)
            if th_id:
                th_drop.append(th_id)
            if fid:
                pair_drop.append(fid)
            # Thermal-only / bare panel features: id is the detection id.
            if not rgb_id and not th_id and fid:
                if thermal_only:
                    th_drop.append(fid)
                else:
                    rgb_drop.append(fid)

        rgb_drop = sorted(set(rgb_drop))
        th_drop = sorted(set(th_drop))
        pair_drop = sorted(set(pair_drop))

        if rgb_drop:
            _emit(
                progress,
                log,
                75,
                f"Excluding {len(rgb_drop)} RGB detection panels…",
                level="info",
            )
            apply_panel_selection(root, "rgb", exclude_ids=rgb_drop)
        if th_drop:
            _emit(
                progress,
                log,
                82,
                f"Excluding {len(th_drop)} thermal detection panels…",
                level="info",
            )
            apply_panel_selection(root, "thermal", exclude_ids=th_drop)

    removed_pairs = _prune_extracted_pairs(
        root,
        drop_pair_ids=set(pair_drop),
        drop_rgb=set(rgb_drop),
        drop_th=set(th_drop),
        progress=progress,
        log=log,
    )
    _emit(
        progress,
        log,
        100,
        f"Done — isolated_in_view={len(isolated_ids)} "
        f"RGB={len(rgb_drop)} thermal={len(th_drop)} pairs={removed_pairs}",
        level="info",
    )
    return {
        "ok": True,
        "removed_rgb": len(rgb_drop),
        "removed_thermal": len(th_drop),
        "removed_pairs": removed_pairs,
        "isolated_in_view": len(isolated_ids),
        "rgb_ids": rgb_drop,
        "thermal_ids": th_drop,
        "tol_frac": tol_frac,
        "view": view_label,
    }
