"""
Advanced validation Step 2: border outlier pruning.

Spatial cardinal neighbors at ±pitch. Iteratively peels dangling cells
(degree < 2) so isolated spurs above/beside the array are removed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from openpvscope.detection.refine_grid import _nearest_in_tol_box


def count_spatial_cardinal_neighbors(
    centers: np.ndarray,
    tree: cKDTree,
    target_idx: int,
    step_x: float,
    step_y: float,
    *,
    tol_frac: float = 0.10,
    active: np.ndarray | None = None,
) -> tuple[int, list[int]]:
    """Count active detections near ±step_x / ±step_y of the target center."""
    pts = np.asarray(centers, dtype=np.float64)
    sx = float(abs(step_x))
    sy = float(abs(step_y))
    side = float(max(sx, sy))
    tol = max(1.0, float(tol_frac) * side)
    pi = pts[target_idx]
    neighbor_indices: list[int] = []

    for dx, dy in ((sx, 0.0), (-sx, 0.0), (0.0, sy), (0.0, -sy)):
        pred = pi + np.array([dx, dy], dtype=np.float64)
        j = _nearest_in_tol_box(tree, pts, pred, tol, tol)
        if j is None or j == target_idx:
            continue
        if active is not None and not bool(active[j]):
            continue
        if j in neighbor_indices:
            continue
        neighbor_indices.append(j)

    return len(neighbor_indices), neighbor_indices


def remove_border_outliers_with_fitted_grids(
    detections: list[dict[str, Any]],
    cluster_grid_fits: dict[int, dict | None],
    *,
    min_main_grid_size: int = 6,
    tol: float = 0.4,
    fine_tuning_confidence_threshold: float = 0.65,
    keep_high_conf_outliers: bool = False,
    tol_frac: float = 0.10,
    max_peel_iters: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _ = (tol, max_peel_iters)
    if not detections:
        return [], []

    clusters: dict[int, list[tuple[int, dict]]] = {}
    for i, det in enumerate(detections):
        if det.get("is_grid_aligned", False):
            cid = int(det.get("cluster_id", -1))
            clusters.setdefault(cid, []).append((i, det))

    enhanced = [dict(d) for d in detections]
    removed_by_border: list[dict[str, Any]] = []

    for cluster_id, cluster_list in clusters.items():
        if len(cluster_list) < min_main_grid_size:
            continue
        grid_params = cluster_grid_fits.get(cluster_id)
        if not grid_params:
            continue

        step_x = float(
            grid_params.get("pitch_seed_dx") or grid_params.get("delta_x") or 0.0
        )
        step_y = float(
            grid_params.get("pitch_seed_dy") or grid_params.get("delta_y") or 0.0
        )
        if step_x < 1.0 or step_y < 1.0:
            continue

        cluster_indices = [idx for idx, _ in cluster_list]
        cluster_detections = [det for _, det in cluster_list]
        n_c = len(cluster_detections)
        centers = np.array(
            [[x + w / 2.0, y + h / 2.0] for x, y, w, h in (det["bbox_pixels"] for det in cluster_detections)],
            dtype=np.float64,
        )
        tree = cKDTree(centers)
        active = np.ones(n_c, dtype=bool)

        # Single-pass peel only. Iterating degree<2 cascades through any row that
        # lacks vertical links (e.g. middle row walk-rejected) and wipes valid panels.
        to_drop: list[int] = []
        for j in range(n_c):
            panel_confidence = float(cluster_detections[j].get("confidence") or 0.0)
            neighbor_count, neighbor_idxs = count_spatial_cardinal_neighbors(
                centers,
                tree,
                j,
                step_x,
                step_y,
                tol_frac=tol_frac,
                active=active,
            )
            should_remove = False
            if neighbor_count < 2:
                if keep_high_conf_outliers:
                    should_remove = panel_confidence < fine_tuning_confidence_threshold
                else:
                    should_remove = True
            elif neighbor_count == 2:
                neighbor_confidences = [
                    float(cluster_detections[ni].get("confidence") or 0.0)
                    for ni in neighbor_idxs
                ]
                low_conf_neighbors = sum(
                    1 for nc in neighbor_confidences if nc < fine_tuning_confidence_threshold
                )
                if panel_confidence < fine_tuning_confidence_threshold and low_conf_neighbors > 0:
                    should_remove = True
            if should_remove:
                to_drop.append(j)
        for j in to_drop:
            active[j] = False

        for j, idx in enumerate(cluster_indices):
            if active[j]:
                enhanced[idx]["is_main_grid"] = True
                enhanced[idx]["border_outlier"] = False
                enhanced[idx]["fate"] = "kept"
            else:
                enhanced[idx]["is_main_grid"] = False
                enhanced[idx]["border_outlier"] = True
                enhanced[idx]["fate"] = "border_prune"
                removed_by_border.append(enhanced[idx])

    for det in enhanced:
        if "is_main_grid" not in det:
            cid = int(det.get("cluster_id", -1))
            has_fit = cid >= 0 and bool(cluster_grid_fits.get(cid))
            if has_fit:
                det["is_main_grid"] = bool(det.get("is_grid_aligned")) and not det.get(
                    "border_outlier", False
                )
                det.setdefault("border_outlier", False)
                if det.get("border_outlier"):
                    det["fate"] = "border_prune"
                else:
                    det.setdefault("fate", "kept")
            else:
                det["is_main_grid"] = False
                det.setdefault("border_outlier", False)

    return enhanced, removed_by_border
