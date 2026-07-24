"""
Advanced validation Step 3: Conway-style fill / restore.

Spatial holes at ±pitch from kept centers (not only LS grid indices), so
slightly warped orthos still restore/synthesize missing interior panels.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from openpvscope.detection.refine_grid import _nearest_in_tol_box, get_grid_indices_from_params


def fill_missing_panels_conway_style(
    detections: list[dict[str, Any]],
    cluster_grid_fits: dict[int, dict | None],
    *,
    removed_by_cluster: dict[int, list] | None = None,
    removed_by_border: list[dict[str, Any]] | None = None,
    min_main_grid_size: int = 6,
    tol: float = 0.4,
    fill_confidence: float = 0.5,
    fine_tuning_confidence_threshold: float = 0.65,
    tol_frac: float = 0.10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _ = tol
    if not detections:
        return detections, []

    # Spatial buckets of removed panels for restore (per cluster)
    removed_pts: dict[int, list[dict[str, Any]]] = {}
    if removed_by_cluster:
        for cluster_id, removed_panels in removed_by_cluster.items():
            removed_pts.setdefault(int(cluster_id), []).extend(removed_panels)
    if removed_by_border:
        for panel in removed_by_border:
            cid = int(panel.get("cluster_id", -1))
            removed_pts.setdefault(cid, []).append(panel)

    clusters: dict[int, list[tuple[int, dict]]] = {}
    for i, det in enumerate(detections):
        if det.get("is_main_grid", False):
            cid = int(det.get("cluster_id", -1))
            clusters.setdefault(cid, []).append((i, det))

    filled = [dict(d) for d in detections]
    new_panels: list[dict[str, Any]] = []

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

        cluster_detections = [det for _, det in cluster_list]
        centers = np.array(
            [[x + w / 2.0, y + h / 2.0] for x, y, w, h in (det["bbox_pixels"] for det in cluster_detections)],
            dtype=np.float64,
        )
        side = float(max(step_x, step_y))
        hole_tol = max(1.0, float(tol_frac) * side)
        tree_kept = cKDTree(centers)

        rem = removed_pts.get(int(cluster_id), [])
        rem_centers = None
        rem_tree = None
        if rem:
            rem_centers = np.array(
                [[p["bbox_pixels"][0] + p["bbox_pixels"][2] / 2.0,
                  p["bbox_pixels"][1] + p["bbox_pixels"][3] / 2.0]
                 for p in rem],
                dtype=np.float64,
            )
            rem_tree = cKDTree(rem_centers)

        widths = [det["bbox_pixels"][2] for det in cluster_detections]
        heights = [det["bbox_pixels"][3] for det in cluster_detections]
        med_w = float(np.median(widths))
        med_h = float(np.median(heights))

        # Candidate holes = one pitch from each kept center (cardinal)
        # Dedup by quantizing to ~tol/2
        quant = max(hole_tol * 0.5, 1.0)
        seen_holes: set[tuple[int, int]] = set()
        hole_preds: list[np.ndarray] = []

        steps = (
            (step_x, 0.0),
            (-step_x, 0.0),
            (0.0, step_y),
            (0.0, -step_y),
        )
        for c in centers:
            for dx, dy in steps:
                pred = c + np.array([dx, dy], dtype=np.float64)
                key = (int(round(pred[0] / quant)), int(round(pred[1] / quant)))
                if key in seen_holes:
                    continue
                seen_holes.add(key)
                hole_preds.append(pred)

        # Also run classic grid-index pass for cells inside the index bbox
        # (covers holes that spatial one-hop from kept might miss when indices align).
        grid_coords = get_grid_indices_from_params(centers, grid_params)
        grid_coords_int = np.round(grid_coords).astype(int)
        grid_to_det: dict[tuple[int, int], int] = {}
        for i, pos in enumerate(grid_coords_int):
            key = (int(pos[0]), int(pos[1]))
            prev = grid_to_det.get(key)
            if prev is None or float(cluster_detections[i].get("confidence") or 0) > float(
                cluster_detections[prev].get("confidence") or 0
            ):
                grid_to_det[key] = i
        occupied = set(grid_to_det.keys())
        if len(grid_coords_int):
            min_x, max_x = int(grid_coords_int[:, 0].min()), int(grid_coords_int[:, 0].max())
            min_y, max_y = int(grid_coords_int[:, 1].min()), int(grid_coords_int[:, 1].max())
            delta_x = float(grid_params["delta_x"])
            delta_y = float(grid_params["delta_y"])
            t_x = float(grid_params.get("translation_x", 0))
            t_y = float(grid_params.get("translation_y", 0))
            for gx in range(min_x, max_x + 1):
                for gy in range(min_y, max_y + 1):
                    if (gx, gy) in occupied:
                        continue
                    pred = np.array([gx * delta_x - t_x, gy * delta_y - t_y], dtype=np.float64)
                    key = (int(round(pred[0] / quant)), int(round(pred[1] / quant)))
                    if key in seen_holes:
                        continue
                    seen_holes.add(key)
                    hole_preds.append(pred)

        # Bounding box of kept centers — do not grow the array outward
        min_c = centers.min(axis=0) - 0.55 * np.array([step_x, step_y])
        max_c = centers.max(axis=0) + 0.55 * np.array([step_x, step_y])

        for pred in hole_preds:
            if pred[0] < min_c[0] or pred[0] > max_c[0] or pred[1] < min_c[1] or pred[1] > max_c[1]:
                continue
            # Already occupied by a kept detection?
            if _nearest_in_tol_box(tree_kept, centers, pred, hole_tol, hole_tol) is not None:
                continue

            # Count spatial cardinal neighbors around the hole
            neighbor_confidences: list[float] = []
            for dx, dy in steps:
                npred = pred + np.array([dx, dy], dtype=np.float64)
                j = _nearest_in_tol_box(tree_kept, centers, npred, hole_tol, hole_tol)
                if j is None:
                    continue
                neighbor_confidences.append(float(cluster_detections[j].get("confidence") or 0.0))
            neighbor_count = len(neighbor_confidences)
            should_fill = False
            if neighbor_count >= 3:
                should_fill = True
            elif neighbor_count == 2:
                should_fill = all(c >= fine_tuning_confidence_threshold for c in neighbor_confidences)
            if not should_fill:
                continue

            original = None
            if rem_tree is not None and rem_centers is not None:
                rj = _nearest_in_tol_box(rem_tree, rem_centers, pred, hole_tol, hole_tol)
                if rj is not None:
                    original = rem[rj]

            if original:
                new_det = dict(original)
                new_det.update(
                    {
                        "bbox_pixels": list(original["bbox_pixels"]),
                        "bbox": list(original["bbox_pixels"]),
                        "is_grid_aligned": True,
                        "is_main_grid": True,
                        "border_outlier": False,
                        "filled_panel": True,
                        "restored_panel": True,
                        "cluster_id": cluster_id,
                        "confidence": float(original.get("confidence") or fill_confidence),
                        "source": "restored_original",
                        "fate": "filled_restored",
                    }
                )
            else:
                w, h = med_w, med_h
                x = float(pred[0] - w / 2.0)
                y = float(pred[1] - h / 2.0)
                new_det = {
                    "bbox_pixels": [x, y, w, h],
                    "bbox": [x, y, w, h],
                    "is_grid_aligned": True,
                    "is_main_grid": True,
                    "border_outlier": False,
                    "filled_panel": True,
                    "restored_panel": False,
                    "cluster_id": cluster_id,
                    "confidence": float(fill_confidence),
                    "source": "filled_conway",
                    "fate": "filled_synth",
                }
            filled.append(new_det)
            new_panels.append(new_det)
            # Expand kept set so subsequent holes in this cluster see the fill
            centers = np.vstack([centers, pred.reshape(1, 2)])
            cluster_detections.append(new_det)
            tree_kept = cKDTree(centers)

    return filled, new_panels
