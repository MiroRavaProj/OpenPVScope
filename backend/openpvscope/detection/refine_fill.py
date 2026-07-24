"""
Advanced validation Step 3: Conway-style fill / restore.

Port of utils/detection/panel_filling.py (rotated-space bbox only; geo is done later).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from openpvscope.detection.refine_grid import get_grid_indices_from_params


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not detections:
        return detections, []

    removed_panels_lookup: dict[int, dict[tuple[int, int], dict]] = {}

    if removed_by_cluster:
        for cluster_id, removed_panels in removed_by_cluster.items():
            grid_params = cluster_grid_fits.get(int(cluster_id))
            if not grid_params:
                continue
            bucket = removed_panels_lookup.setdefault(int(cluster_id), {})
            for panel in removed_panels:
                x, y, w, h = panel["bbox_pixels"]
                center = np.array([[x + w / 2.0, y + h / 2.0]], dtype=np.float64)
                g = get_grid_indices_from_params(center, grid_params)
                pos = tuple(int(v) for v in np.round(g[0]).astype(int))
                bucket[pos] = panel

    if removed_by_border:
        for panel in removed_by_border:
            cluster_id = int(panel.get("cluster_id", -1))
            grid_params = cluster_grid_fits.get(cluster_id)
            if not grid_params:
                continue
            bucket = removed_panels_lookup.setdefault(cluster_id, {})
            x, y, w, h = panel["bbox_pixels"]
            center = np.array([[x + w / 2.0, y + h / 2.0]], dtype=np.float64)
            g = get_grid_indices_from_params(center, grid_params)
            pos = tuple(int(v) for v in np.round(g[0]).astype(int))
            prev = bucket.get(pos)
            if prev is None or float(panel.get("confidence") or 0) > float(prev.get("confidence") or 0):
                bucket[pos] = panel

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

        cluster_detections = [det for _, det in cluster_list]
        centers = np.array(
            [[x + w / 2.0, y + h / 2.0] for x, y, w, h in (det["bbox_pixels"] for det in cluster_detections)],
            dtype=np.float64,
        )
        grid_coords = get_grid_indices_from_params(centers, grid_params)
        grid_coords_int = np.round(grid_coords).astype(int)
        # One index per rounded cell (highest confidence wins) so occupied ⊆ grid_to_det
        grid_to_det: dict[tuple[int, int], int] = {}
        for i, pos in enumerate(grid_coords_int):
            key = (int(pos[0]), int(pos[1]))
            prev = grid_to_det.get(key)
            if prev is None:
                grid_to_det[key] = i
            else:
                c_new = float(cluster_detections[i].get("confidence") or 0.0)
                c_old = float(cluster_detections[prev].get("confidence") or 0.0)
                if c_new > c_old:
                    grid_to_det[key] = i
        occupied = set(grid_to_det.keys())

        min_x, max_x = int(grid_coords_int[:, 0].min()), int(grid_coords_int[:, 0].max())
        min_y, max_y = int(grid_coords_int[:, 1].min()), int(grid_coords_int[:, 1].max())

        for gx in range(min_x, max_x + 1):
            for gy in range(min_y, max_y + 1):
                if (gx, gy) in occupied:
                    continue
                neighbor_confidences: list[float] = []
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    npos = (gx + dx, gy + dy)
                    if npos not in occupied:
                        continue
                    # Legacy never mutates occupied mid-loop; still guard against
                    # duplicate rounded cells missing from the index map.
                    di = grid_to_det.get(npos)
                    if di is None:
                        continue
                    neighbor_confidences.append(float(cluster_detections[di].get("confidence") or 0.0))
                neighbor_count = len(neighbor_confidences)
                should_fill = False
                if neighbor_count >= 3:
                    should_fill = True
                elif neighbor_count == 2:
                    should_fill = all(c >= fine_tuning_confidence_threshold for c in neighbor_confidences)
                if not should_fill:
                    continue

                original = removed_panels_lookup.get(cluster_id, {}).get((gx, gy))
                if original:
                    new_det = dict(original)
                    new_det.update(
                        {
                            "bbox_pixels": list(original["bbox_pixels"]),
                            "is_grid_aligned": True,
                            "is_main_grid": True,
                            "border_outlier": False,
                            "filled_panel": True,
                            "restored_panel": True,
                            "cluster_id": cluster_id,
                            "confidence": float(original.get("confidence") or fill_confidence),
                            "source": "restored_original",
                        }
                    )
                else:
                    delta_x = float(grid_params["delta_x"])
                    delta_y = float(grid_params["delta_y"])
                    t_x = float(grid_params.get("translation_x", 0))
                    t_y = float(grid_params.get("translation_y", 0))
                    cx = gx * delta_x - t_x
                    cy = gy * delta_y - t_y
                    widths = [det["bbox_pixels"][2] for det in cluster_detections]
                    heights = [det["bbox_pixels"][3] for det in cluster_detections]
                    w = float(np.median(widths))
                    h = float(np.median(heights))
                    x = cx - w / 2.0
                    y = cy - h / 2.0
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
                    }
                filled.append(new_det)
                new_panels.append(new_det)
                # Do not update occupied/grid_to_det mid-pass (legacy behavior).

    return filled, new_panels
