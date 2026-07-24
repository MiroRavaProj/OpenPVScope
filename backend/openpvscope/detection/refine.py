"""
Orchestrate Advanced Validation (legacy fine-tuning) after NMS.

Steps: DBSCAN+grid fit → border prune → Conway fill/restore.

Default is strict geometric prune (``keep_high_conf_outliers=False``): match
score alone cannot override DBSCAN noise / grid outliers / dangling borders.
Set keep_high_conf_outliers=True to restore legacy soft retention.

Every post-NMS detection is annotated with ``fate`` / ``include``; the full
audit list is returned in ``stats["all_detections"]``.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from openpvscope.detection.refine_border import remove_border_outliers_with_fitted_grids
from openpvscope.detection.refine_fill import fill_missing_panels_conway_style
from openpvscope.detection.refine_grid import advanced_grid_validation_bruteforce

ProgressCb = Callable[[float | None, str], None]

DEFAULT_INCLUDE_FATES = frozenset(
    {"kept", "filled_restored", "filled_synth", "readded"}
)


def _normalize_det(det: dict[str, Any]) -> dict[str, Any]:
    """Ensure bbox_pixels exists (OpenPVScope match uses 'bbox')."""
    d = dict(det)
    if "bbox_pixels" not in d:
        bbox = d.get("bbox")
        if bbox is None or len(bbox) < 4:
            raise ValueError("detection missing bbox")
        d["bbox_pixels"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
    else:
        bp = d["bbox_pixels"]
        d["bbox_pixels"] = [float(bp[0]), float(bp[1]), float(bp[2]), float(bp[3])]
    d.setdefault("confidence", float(d.get("confidence") or 0.0))
    return d


def _median_panel_size(dets: list[dict[str, Any]], fallback_w: float, fallback_h: float) -> tuple[float, float]:
    """Prefer median detection bbox over template mean (more stable on mixed templates)."""
    ws = [float(d["bbox_pixels"][2]) for d in dets if d.get("bbox_pixels")]
    hs = [float(d["bbox_pixels"][3]) for d in dets if d.get("bbox_pixels")]
    if len(ws) < 3:
        return float(fallback_w), float(fallback_h)
    return float(np.median(ws)), float(np.median(hs))


def _center(det: dict[str, Any]) -> tuple[float, float]:
    x, y, w, h = det["bbox_pixels"]
    return x + w / 2.0, y + h / 2.0


def _center_key(det: dict[str, Any], quant: float = 0.5) -> tuple[int, int]:
    cx, cy = _center(det)
    return int(round(cx / quant)), int(round(cy / quant))


def _ensure_kept_fate(det: dict[str, Any]) -> None:
    if det.get("restored_panel"):
        det["fate"] = "filled_restored"
    elif det.get("filled_panel"):
        det["fate"] = "filled_synth"
    else:
        det.setdefault("fate", "kept")


def _build_all_detections(
    validated: list[dict[str, Any]],
    removed_border: list[dict[str, Any]],
    kept: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union of kept (include) + rejected post-NMS (exclude), keyed by center."""
    all_dets: list[dict[str, Any]] = []
    included_keys: set[tuple[int, int]] = set()

    for d in kept:
        dd = dict(d)
        _ensure_kept_fate(dd)
        dd["include"] = True
        all_dets.append(dd)
        included_keys.add(_center_key(dd))

    def _add_reject(src: dict[str, Any], default_fate: str) -> None:
        k = _center_key(src)
        if k in included_keys:
            return
        for existing in all_dets:
            if _center_key(existing) == k:
                return
        dd = dict(src)
        dd["include"] = False
        dd.setdefault("fate", default_fate)
        all_dets.append(dd)

    for d in validated:
        if _center_key(d) in included_keys:
            continue
        if d.get("is_grid_aligned") and not d.get("border_outlier"):
            # Survived step1 but dropped later (border) — handled via removed_border
            continue
        fate = str(d.get("fate") or "walk_reject")
        _add_reject(d, fate)

    for d in removed_border:
        _add_reject(d, "border_prune")

    return all_dets


def run_advanced_validation(
    detections: list[dict[str, Any]],
    template_width: float,
    template_height: float,
    *,
    fine_tuning_confidence_threshold: float = 0.65,
    min_samples: int = 4,
    min_cluster_size: int = 12,
    delta_jitter: float = 0.03,
    n_translations: int = 2000,
    fill_confidence: float = 0.5,
    keep_high_conf_outliers: bool = False,
    walk_tol_frac: float = 0.10,
    pitch_slack: float = 0.05,
    progress: ProgressCb | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Returns (kept detections in rotated space, stats).

    ``min_samples`` = DBSCAN core-point density (keep small for lattices).
    ``min_cluster_size`` = drop clusters with fewer than this many panels.
    ``stats["all_detections"]`` holds every post-NMS panel with ``fate`` + ``include``.
    """

    def prog(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    if not detections:
        return [], {
            "input": 0,
            "after_step1": 0,
            "after_step2": 0,
            "after_step3": 0,
            "filled": 0,
            "all_detections": [],
        }

    dets = [_normalize_det(d) for d in detections]
    n0 = len(dets)
    tw, th = _median_panel_size(dets, template_width, template_height)

    prog(None, f"Refine step 1/3: DBSCAN + lattice walk ({n0} panels, Δ≈{tw:.0f}×{th:.0f})")
    validated, cluster_fits, removed_by_cluster, grid_stats = advanced_grid_validation_bruteforce(
        dets,
        tw,
        th,
        min_samples=min_samples,
        min_cluster_size=min_cluster_size,
        delta_jitter=delta_jitter,
        fine_tuning_confidence_threshold=fine_tuning_confidence_threshold,
        n_translations=n_translations,
        keep_high_conf_outliers=keep_high_conf_outliers,
        walk_tol_frac=walk_tol_frac,
        pitch_slack=pitch_slack,
    )
    step1_keep = [d for d in validated if d.get("is_grid_aligned")]
    n1 = len(step1_keep)
    dropped1 = n0 - n1
    prog(
        None,
        f"Refine step 1/3 done: {n0} → {n1} aligned "
        f"(dropped {dropped1}: noise={grid_stats['noise_dropped']}, "
        f"walk_reject={grid_stats['grid_outliers_dropped']}, "
        f"kept_high_conf="
        f"{grid_stats['noise_kept_high_conf'] + grid_stats['grid_outliers_kept_high_conf']}, "
        f"walk_clusters={grid_stats.get('walk_clusters', 0)}, "
        f"seeds={grid_stats.get('walk_seeds', 0)}, "
        f"neighbor_pitch={grid_stats.get('neighbor_pitch', 0)})",
    )

    prog(None, f"Refine step 2/3: border prune ({n1} aligned)")
    enhanced, removed_border = remove_border_outliers_with_fitted_grids(
        step1_keep,
        cluster_fits,
        fine_tuning_confidence_threshold=fine_tuning_confidence_threshold,
        keep_high_conf_outliers=keep_high_conf_outliers,
        tol_frac=walk_tol_frac,
    )
    step2_keep = [d for d in enhanced if not d.get("border_outlier")]
    for d in step2_keep:
        d["is_main_grid"] = True
        d["border_outlier"] = False
        d["fate"] = "kept"
    n2 = len(step2_keep)
    prog(
        None,
        f"Refine step 2/3 done: {n1} → {n2} main-grid (border dropped {len(removed_border)})",
    )

    prog(None, f"Refine step 3/3: Conway fill ({n2} main-grid)")
    filled, new_panels = fill_missing_panels_conway_style(
        step2_keep,
        cluster_fits,
        removed_by_cluster=removed_by_cluster,
        removed_by_border=removed_border,
        fill_confidence=fill_confidence,
        fine_tuning_confidence_threshold=fine_tuning_confidence_threshold,
        tol_frac=walk_tol_frac,
    )

    readded = 0
    if keep_high_conf_outliers:
        # Legacy soft retention: re-add high-conf originals not near a kept panel.
        keep_r = 0.35 * min(tw, th)
        kept_centers = [_center(d) for d in filled if d.get("bbox_pixels")]

        def near_kept(cx: float, cy: float) -> bool:
            r2 = keep_r * keep_r
            for kx, ky in kept_centers:
                if (cx - kx) ** 2 + (cy - ky) ** 2 <= r2:
                    return True
            return False

        for d in validated:
            conf = float(d.get("confidence") or 0.0)
            if conf < fine_tuning_confidence_threshold:
                continue
            if d.get("border_outlier"):
                continue
            cx, cy = _center(d)
            if near_kept(cx, cy):
                continue
            dd = dict(d)
            dd["is_main_grid"] = bool(dd.get("is_grid_aligned"))
            dd["filled_panel"] = False
            dd["fate"] = "readded"
            filled.append(dd)
            kept_centers.append((cx, cy))
            readded += 1

    out: list[dict[str, Any]] = []
    for d in filled:
        bp = d["bbox_pixels"]
        d["bbox"] = [float(bp[0]), float(bp[1]), float(bp[2]), float(bp[3])]
        _ensure_kept_fate(d)
        d["include"] = True
        out.append(d)

    all_detections = _build_all_detections(validated, removed_border, out)

    stats = {
        "input": n0,
        "after_step1": n1,
        "after_step2": n2,
        "after_step3": len(out),
        "filled": len(new_panels),
        "restored": sum(1 for p in new_panels if p.get("restored_panel")),
        "synthesized": sum(1 for p in new_panels if p.get("filled_panel") and not p.get("restored_panel")),
        "readded_high_conf": readded,
        "keep_high_conf_outliers": keep_high_conf_outliers,
        "grid_stats": grid_stats,
        "border_dropped": len(removed_border),
        "grid_delta": [tw, th],
        "fine_tuning_confidence_threshold": fine_tuning_confidence_threshold,
        "all_detections": all_detections,
    }
    prog(
        None,
        f"Refine done: {n0} → {len(out)} (fill +{len(new_panels)}, re-add +{readded}, "
        f"audit={len(all_detections)})",
    )
    return out, stats
