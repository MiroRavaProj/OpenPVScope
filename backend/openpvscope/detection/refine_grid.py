"""
Advanced validation Step 1: DBSCAN clustering + brute-force grid fit.

Port of utils/detection/advanced_validation.py (no NiceGUI / tqdm).
Detections use bbox_pixels = [x, y, w, h] in rotated detection space.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.cluster import DBSCAN


def get_grid_indices_from_params(centers: np.ndarray, grid_params: dict[str, float]) -> np.ndarray:
    """Pixel centers → float grid indices using fitted params."""
    delta_x = float(grid_params["delta_x"])
    delta_y = float(grid_params["delta_y"])
    t_x = float(grid_params.get("translation_x", 0))
    t_y = float(grid_params.get("translation_y", 0))
    aligned = centers + np.array([t_x, t_y], dtype=np.float64)
    return aligned / np.array([delta_x, delta_y], dtype=np.float64)


def fit_grid_bruteforce(
    points: np.ndarray,
    delta_x: float,
    delta_y: float,
    outlier_threshold: float,
    n_translations: int = 2000,
    delta_jitter: float = 0.03,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray | None, dict[str, float] | None]:
    """
    Try many random translations (±jittered spacing); maximize inliers.
    """
    if rng is None:
        rng = np.random.default_rng()
    best_mask: np.ndarray | None = None
    best_params: dict[str, float] | None = None
    max_inliers = -1
    pts = np.asarray(points, dtype=np.float64)

    for _ in range(int(n_translations)):
        jitter_x = float(rng.uniform(-delta_jitter, delta_jitter))
        jitter_y = float(rng.uniform(-delta_jitter, delta_jitter))
        dx = delta_x * (1.0 + jitter_x)
        dy = delta_y * (1.0 + jitter_y)
        t_x = float(rng.uniform(-dx, dx))
        t_y = float(rng.uniform(-dy, dy))
        aligned = pts + np.array([t_x, t_y])
        gx = np.round(aligned[:, 0] / dx) * dx
        gy = np.round(aligned[:, 1] / dy) * dy
        dist = np.hypot(aligned[:, 0] - gx, aligned[:, 1] - gy)
        mask = dist < outlier_threshold
        n_in = int(mask.sum())
        if n_in > max_inliers:
            max_inliers = n_in
            best_mask = mask.copy()
            best_params = {
                "translation_x": t_x,
                "translation_y": t_y,
                "delta_x": dx,
                "delta_y": dy,
                "jitter_x": jitter_x,
                "jitter_y": jitter_y,
            }
    return best_mask, best_params


def advanced_grid_validation_bruteforce(
    detections: list[dict[str, Any]],
    template_width: float,
    template_height: float,
    *,
    eps_pixels: float | None = None,
    min_samples: int = 6,
    delta_jitter: float = 0.03,
    fine_tuning_confidence_threshold: float = 0.65,
    n_translations: int = 2000,
) -> tuple[list[dict[str, Any]], dict[int, dict | None], dict[int, list]]:
    """
    Cluster centers with DBSCAN; fit a translation grid per cluster.
    Low-confidence outliers are marked is_grid_aligned=False.
    """
    if len(detections) < min_samples:
        out = []
        for det in detections:
            d = dict(det)
            d["is_grid_aligned"] = True
            d["cluster_id"] = -1
            out.append(d)
        return out, {}, {}

    tw = float(template_width)
    th = float(template_height)
    if eps_pixels is None:
        eps_pixels = float(max(tw, th) * 1.1)

    centers = []
    for det in detections:
        x, y, w, h = det["bbox_pixels"]
        centers.append([x + w / 2.0, y + h / 2.0])
    centers_arr = np.asarray(centers, dtype=np.float64)

    labels = DBSCAN(eps=float(eps_pixels), min_samples=int(min_samples)).fit_predict(centers_arr)
    validated: list[dict[str, Any]] = []
    for i, det in enumerate(detections):
        d = dict(det)
        d["cluster_id"] = int(labels[i])
        d["is_grid_aligned"] = False
        validated.append(d)

    for i, det in enumerate(validated):
        if det["cluster_id"] == -1:
            conf = float(det.get("confidence") or 0.0)
            det["is_grid_aligned"] = conf >= fine_tuning_confidence_threshold

    cluster_grid_fits: dict[int, dict | None] = {}
    removed_by_cluster: dict[int, list] = {}
    delta_x = tw
    delta_y = th
    outlier_threshold = min(tw, th) * 0.15
    valid_clusters = [c for c in np.unique(labels) if c != -1]

    for cluster_id in valid_clusters:
        cid = int(cluster_id)
        idxs = np.where(labels == cluster_id)[0]
        if len(idxs) < min_samples:
            removed: list = []
            for j in idxs:
                conf = float(validated[j].get("confidence") or 0.0)
                if conf < fine_tuning_confidence_threshold:
                    validated[j]["is_grid_aligned"] = False
                    removed.append(validated[j])
                else:
                    validated[j]["is_grid_aligned"] = True
            removed_by_cluster[cid] = removed
            continue

        mask, params = fit_grid_bruteforce(
            centers_arr[idxs],
            delta_x,
            delta_y,
            outlier_threshold,
            n_translations=n_translations,
            delta_jitter=delta_jitter,
        )
        removed = []
        if mask is not None and params is not None:
            n_in = int(mask.sum())
            cluster_grid_fits[cid] = {
                **params,
                "n_inliers": n_in,
                "n_outliers": int(len(mask) - n_in),
                "inlier_rate": float(100.0 * n_in / max(1, len(mask))),
                "cluster_indices": idxs.tolist(),
            }
            for k, j in enumerate(idxs):
                if mask[k]:
                    validated[j]["is_grid_aligned"] = True
                else:
                    conf = float(validated[j].get("confidence") or 0.0)
                    if conf < fine_tuning_confidence_threshold:
                        validated[j]["is_grid_aligned"] = False
                        removed.append(validated[j])
                    else:
                        validated[j]["is_grid_aligned"] = True
        else:
            cluster_grid_fits[cid] = None
            for j in idxs:
                conf = float(validated[j].get("confidence") or 0.0)
                if conf < fine_tuning_confidence_threshold:
                    validated[j]["is_grid_aligned"] = False
                    removed.append(validated[j])
                else:
                    validated[j]["is_grid_aligned"] = True
        removed_by_cluster[cid] = removed

    return validated, cluster_grid_fits, removed_by_cluster
