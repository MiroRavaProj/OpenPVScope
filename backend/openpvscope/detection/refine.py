"""
Orchestrate Advanced Validation (legacy fine-tuning) after NMS.

Steps: DBSCAN+grid fit → border prune → Conway fill/restore.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from openpvscope.detection.refine_border import remove_border_outliers_with_fitted_grids
from openpvscope.detection.refine_fill import fill_missing_panels_conway_style
from openpvscope.detection.refine_grid import advanced_grid_validation_bruteforce

ProgressCb = Callable[[float | None, str], None]


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


def run_advanced_validation(
    detections: list[dict[str, Any]],
    template_width: float,
    template_height: float,
    *,
    fine_tuning_confidence_threshold: float = 0.65,
    min_samples: int = 6,
    delta_jitter: float = 0.03,
    n_translations: int = 2000,
    fill_confidence: float = 0.5,
    progress: ProgressCb | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Returns (kept detections in rotated space, stats).

    Softened vs a strict main-grid-only export: high-confidence NMS peaks that
    fail grid alignment are retained in the final set (legacy kept them when
    conf >= threshold; we also keep them even if they skip border/fill).
    """

    def prog(p: float | None, msg: str) -> None:
        if progress:
            progress(p, msg)

    if not detections:
        return [], {"input": 0, "after_step1": 0, "after_step2": 0, "after_step3": 0, "filled": 0}

    dets = [_normalize_det(d) for d in detections]
    n0 = len(dets)
    tw, th = _median_panel_size(dets, template_width, template_height)

    prog(None, f"Refine step 1/3: DBSCAN + grid fit ({n0} panels, Δ≈{tw:.0f}×{th:.0f})")
    validated, cluster_fits, removed_by_cluster = advanced_grid_validation_bruteforce(
        dets,
        tw,
        th,
        min_samples=min_samples,
        delta_jitter=delta_jitter,
        fine_tuning_confidence_threshold=fine_tuning_confidence_threshold,
        n_translations=n_translations,
    )
    step1_keep = [d for d in validated if d.get("is_grid_aligned")]
    # High-conf rejects are already marked aligned; low-conf rejects stay out.
    n1 = len(step1_keep)

    prog(None, f"Refine step 2/3: border prune ({n1} aligned)")
    enhanced, removed_border = remove_border_outliers_with_fitted_grids(
        step1_keep,
        cluster_fits,
        fine_tuning_confidence_threshold=fine_tuning_confidence_threshold,
    )
    step2_keep = [d for d in enhanced if not d.get("border_outlier")]
    for d in step2_keep:
        d["is_main_grid"] = True
        d["border_outlier"] = False
    n2 = len(step2_keep)

    prog(None, f"Refine step 3/3: Conway fill ({n2} main-grid)")
    filled, new_panels = fill_missing_panels_conway_style(
        step2_keep,
        cluster_fits,
        removed_by_cluster=removed_by_cluster,
        removed_by_border=removed_border,
        fill_confidence=fill_confidence,
        fine_tuning_confidence_threshold=fine_tuning_confidence_threshold,
    )

    # Re-attach high-confidence originals that were dropped only as low-conf
    # grid/border rejects? Those stay out. But keep any step1 aligned panel
    # that border removed despite high conf — border step already keeps those.
    #
    # Soft retention: any original with conf >= threshold whose center is not
    # within 0.35×min(tw,th) of a kept panel is re-added (covers high-conf
    # noise / small clusters that never got a grid fit).
    keep_r = 0.35 * min(tw, th)
    kept_centers = [_center(d) for d in filled if d.get("bbox_pixels")]

    def near_kept(cx: float, cy: float) -> bool:
        r2 = keep_r * keep_r
        for kx, ky in kept_centers:
            if (cx - kx) ** 2 + (cy - ky) ** 2 <= r2:
                return True
        return False

    readded = 0
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
        filled.append(dd)
        kept_centers.append((cx, cy))
        readded += 1

    out: list[dict[str, Any]] = []
    for d in filled:
        bp = d["bbox_pixels"]
        d["bbox"] = [float(bp[0]), float(bp[1]), float(bp[2]), float(bp[3])]
        out.append(d)

    stats = {
        "input": n0,
        "after_step1": n1,
        "after_step2": n2,
        "after_step3": len(out),
        "filled": len(new_panels),
        "restored": sum(1 for p in new_panels if p.get("restored_panel")),
        "synthesized": sum(1 for p in new_panels if p.get("filled_panel") and not p.get("restored_panel")),
        "readded_high_conf": readded,
        "grid_delta": [tw, th],
        "fine_tuning_confidence_threshold": fine_tuning_confidence_threshold,
    }
    prog(
        None,
        f"Refine done: {n0} → {len(out)} (fill +{len(new_panels)}, re-add +{readded})",
    )
    return out, stats
