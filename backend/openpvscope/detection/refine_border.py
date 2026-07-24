"""
Advanced validation Step 2: border outlier pruning.

Port of utils/detection/border_filtering.py.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from openpvscope.detection.refine_grid import get_grid_indices_from_params


def count_neighbors_and_get_info(
    grid_coords: np.ndarray,
    grid_coords_int: np.ndarray,
    cluster_detections: list[dict[str, Any]],
    target_idx: int,
    tol: float = 0.4,
) -> tuple[int, list[float], list[int]]:
    target = tuple(int(v) for v in grid_coords_int[target_idx])
    gx, gy = target
    neighbor_indices: list[int] = []
    neighbor_confidences: list[float] = []

    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        diffs = grid_coords - np.array([gx + dx, gy + dy], dtype=np.float64)
        distance_mask = (np.abs(diffs[:, 0]) <= tol) & (np.abs(diffs[:, 1]) <= tol)
        neighbor_idx_array = np.where(distance_mask)[0]
        if len(neighbor_idx_array) > 0:
            distances = np.linalg.norm(diffs[distance_mask], axis=1)
            closest_idx = int(neighbor_idx_array[int(np.argmin(distances))])
            neighbor_indices.append(closest_idx)
            neighbor_confidences.append(float(cluster_detections[closest_idx].get("confidence") or 0.0))

    return len(neighbor_confidences), neighbor_confidences, neighbor_indices


def remove_border_outliers_with_fitted_grids(
    detections: list[dict[str, Any]],
    cluster_grid_fits: dict[int, dict | None],
    *,
    min_main_grid_size: int = 6,
    tol: float = 0.4,
    fine_tuning_confidence_threshold: float = 0.65,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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

        cluster_indices = [idx for idx, _ in cluster_list]
        cluster_detections = [det for _, det in cluster_list]
        centers = np.array(
            [[x + w / 2.0, y + h / 2.0] for x, y, w, h in (det["bbox_pixels"] for det in cluster_detections)],
            dtype=np.float64,
        )
        grid_coords = get_grid_indices_from_params(centers, grid_params)
        grid_coords_int = np.round(grid_coords).astype(int)

        for j, idx in enumerate(cluster_indices):
            panel_confidence = float(cluster_detections[j].get("confidence") or 0.0)
            neighbor_count, neighbor_confidences, _ = count_neighbors_and_get_info(
                grid_coords, grid_coords_int, cluster_detections, j, tol=tol
            )
            should_keep = True
            if neighbor_count < 2:
                if panel_confidence < fine_tuning_confidence_threshold:
                    should_keep = False
            elif neighbor_count == 2:
                low_conf_neighbors = sum(
                    1 for nc in neighbor_confidences if nc < fine_tuning_confidence_threshold
                )
                if panel_confidence < fine_tuning_confidence_threshold and low_conf_neighbors > 0:
                    should_keep = False

            if should_keep:
                enhanced[idx]["is_main_grid"] = True
                enhanced[idx]["border_outlier"] = False
            else:
                if panel_confidence < fine_tuning_confidence_threshold:
                    enhanced[idx]["is_main_grid"] = False
                    enhanced[idx]["border_outlier"] = True
                    removed_by_border.append(enhanced[idx])
                else:
                    enhanced[idx]["is_main_grid"] = True
                    enhanced[idx]["border_outlier"] = False

    # Panels never touched: if still aligned but no is_main_grid, treat as main when aligned
    for det in enhanced:
        if "is_main_grid" not in det:
            det["is_main_grid"] = bool(det.get("is_grid_aligned")) and not det.get("border_outlier", False)
            det.setdefault("border_outlier", False)

    return enhanced, removed_by_border
