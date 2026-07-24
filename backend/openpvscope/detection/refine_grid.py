"""
Advanced validation Step 1: DBSCAN + local lattice walk.

Keep panels that are reachable by stepping ±pitch in X/Y from dense 2D seeds
(recenter every hop — no cumulative global-grid drift).

No 2× gap jumps: a missing panel is skipped by walking around via adjacent
rows/columns from other seeds; Conway fill later handles true holes.

Border prune / Conway fill still need pitch+origin metadata: after the walk,
least-squares grid params are fit on *kept* points only (not used to reject).
"""

from __future__ import annotations

import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
from sklearn.cluster import DBSCAN


def get_grid_indices_from_params(centers: np.ndarray, grid_params: dict[str, float]) -> np.ndarray:
    """Pixel centers → float grid indices using fitted params."""
    delta_x = float(grid_params["delta_x"])
    delta_y = float(grid_params["delta_y"])
    t_x = float(grid_params.get("translation_x", 0))
    t_y = float(grid_params.get("translation_y", 0))
    aligned = centers + np.array([t_x, t_y], dtype=np.float64)
    return aligned / np.array([delta_x, delta_y], dtype=np.float64)


def estimate_pitch_from_neighbors(
    points: np.ndarray,
    expected_dx: float,
    expected_dy: float,
    *,
    rel_lo: float = 0.55,
    rel_hi: float = 1.45,
    ortho_frac: float = 0.35,
    min_gaps: int = 3,
) -> tuple[float, float, dict[str, Any]]:
    """Median center-to-center gap along rows (dx) and columns (dy)."""
    pts = np.asarray(points, dtype=np.float64)
    meta: dict[str, Any] = {
        "seed_dx": float(expected_dx),
        "seed_dy": float(expected_dy),
        "n_dx_gaps": 0,
        "n_dy_gaps": 0,
        "used_neighbors": False,
    }
    if len(pts) < 4 or expected_dx <= 1 or expected_dy <= 1:
        return float(expected_dx), float(expected_dy), meta

    lo_x, hi_x = expected_dx * rel_lo, expected_dx * rel_hi
    lo_y, hi_y = expected_dy * rel_lo, expected_dy * rel_hi
    max_ortho_x = ortho_frac * expected_dy
    max_ortho_y = ortho_frac * expected_dx

    xs: list[float] = []
    ys: list[float] = []
    n = len(pts)
    for i in range(n):
        for j in range(i + 1, n):
            dx = abs(float(pts[j, 0] - pts[i, 0]))
            dy = abs(float(pts[j, 1] - pts[i, 1]))
            if dy <= max_ortho_x and lo_x <= dx <= hi_x:
                xs.append(dx)
            if dx <= max_ortho_y and lo_y <= dy <= hi_y:
                ys.append(dy)

    meta["n_dx_gaps"] = len(xs)
    meta["n_dy_gaps"] = len(ys)
    dx = float(np.median(xs)) if len(xs) >= min_gaps else float(expected_dx)
    dy = float(np.median(ys)) if len(ys) >= min_gaps else float(expected_dy)
    meta["used_neighbors"] = len(xs) >= min_gaps or len(ys) >= min_gaps
    meta["seed_dx"] = dx
    meta["seed_dy"] = dy
    return dx, dy, meta


def _classify_inliers(
    points: np.ndarray,
    *,
    delta_x: float,
    delta_y: float,
    translation_x: float,
    translation_y: float,
    outlier_threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pts = np.asarray(points, dtype=np.float64)
    aligned = pts + np.array([translation_x, translation_y], dtype=np.float64)
    cols = np.round(aligned[:, 0] / delta_x)
    rows = np.round(aligned[:, 1] / delta_y)
    gx = cols * delta_x
    gy = rows * delta_y
    dist = np.hypot(aligned[:, 0] - gx, aligned[:, 1] - gy)
    mask = dist < float(outlier_threshold)
    return mask, cols.astype(np.int64), rows.astype(np.int64)


def refit_grid_least_squares(
    points: np.ndarray,
    params: dict[str, float],
    outlier_threshold: float,
    *,
    inlier_mask: np.ndarray | None = None,
    pitch_slack: float = 0.30,
) -> tuple[np.ndarray, dict[str, float]]:
    """Fit pitch/origin on points (for fill/border metadata). Does not decide keep."""
    pts = np.asarray(points, dtype=np.float64)
    dx0 = float(params["delta_x"])
    dy0 = float(params["delta_y"])
    tx0 = float(params.get("translation_x", 0.0))
    ty0 = float(params.get("translation_y", 0.0))

    soft_thresh = max(float(outlier_threshold) * 2.5, float(outlier_threshold) + 8.0)
    mask0, cols, rows = _classify_inliers(
        pts,
        delta_x=dx0,
        delta_y=dy0,
        translation_x=tx0,
        translation_y=ty0,
        outlier_threshold=soft_thresh,
    )
    if inlier_mask is not None:
        use = np.asarray(inlier_mask, dtype=bool) & mask0
    else:
        use = mask0
    if int(use.sum()) < 4:
        use = np.ones(len(pts), dtype=bool)

    c = cols[use].astype(np.float64)
    r = rows[use].astype(np.float64)
    x = pts[use, 0]
    y = pts[use, 1]

    dx, tx = dx0, tx0
    dy, ty = dy0, ty0

    if len(np.unique(np.round(c).astype(np.int64))) >= 2:
        a = np.column_stack([c, -np.ones_like(c)])
        sol, _, _, _ = np.linalg.lstsq(a, x, rcond=None)
        dx_c, tx_c = float(sol[0]), float(sol[1])
        if (1.0 - pitch_slack) * dx0 <= dx_c <= (1.0 + pitch_slack) * dx0 and dx_c > 1.0:
            dx, tx = dx_c, tx_c

    if len(np.unique(np.round(r).astype(np.int64))) >= 2:
        a = np.column_stack([r, -np.ones_like(r)])
        sol, _, _, _ = np.linalg.lstsq(a, y, rcond=None)
        dy_c, ty_c = float(sol[0]), float(sol[1])
        if (1.0 - pitch_slack) * dy0 <= dy_c <= (1.0 + pitch_slack) * dy0 and dy_c > 1.0:
            dy, ty = dy_c, ty_c

    mask, _, _ = _classify_inliers(
        pts,
        delta_x=dx,
        delta_y=dy,
        translation_x=tx,
        translation_y=ty,
        outlier_threshold=outlier_threshold,
    )
    return mask, {
        "translation_x": tx,
        "translation_y": ty,
        "delta_x": dx,
        "delta_y": dy,
        "jitter_x": float(params.get("jitter_x", 0.0)),
        "jitter_y": float(params.get("jitter_y", 0.0)),
    }


def _nearest_in_tol_box(
    tree: cKDTree,
    pts: np.ndarray,
    pred: np.ndarray,
    tol_x: float,
    tol_y: float,
) -> int | None:
    """Nearest point inside axis-aligned ±tol_x / ±tol_y of ``pred``, or None."""
    r = float(np.hypot(tol_x, tol_y))
    if r < 1e-9:
        return None
    candidates = tree.query_ball_point(pred, r)
    if not candidates:
        return None
    best_j: int | None = None
    best_d2 = float("inf")
    px, py = float(pred[0]), float(pred[1])
    for j in candidates:
        dx = abs(float(pts[j, 0]) - px)
        dy = abs(float(pts[j, 1]) - py)
        if dx <= tol_x and dy <= tol_y:
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_j = int(j)
    return best_j


def local_lattice_walk(
    points: np.ndarray,
    step_x: float,
    step_y: float,
    *,
    panel_width: float | None = None,
    panel_height: float | None = None,
    tol_frac: float = 0.10,
    tol_x: float | None = None,
    tol_y: float | None = None,
    tol: float | None = None,
    require_2d_seed: bool = True,
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Multi-source BFS: from 2D cores, step ±step_x / ±step_y; accept nearest
    detection inside an anisotropic box (±tol_x, ±tol_y); recenter each hop.

    Default tolerance: isotropic ±10% of the largest panel/pitch side
    (helps when the ortho is slightly warped). Legacy ``tol`` sets both axes.

    No 2× gap jumps — missing cells are reached around via other branches.
    """
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    meta: dict[str, Any] = {
        "n_seeds": 0,
        "n_kept": 0,
        "step_x": float(step_x),
        "step_y": float(step_y),
        "tol": 0.0,
        "tol_x": 0.0,
        "tol_y": 0.0,
        "tol_frac": float(tol_frac),
    }
    if n == 0:
        return np.zeros(0, dtype=bool), meta
    if n == 1:
        keep = np.ones(1, dtype=bool)
        meta["n_seeds"] = 1
        meta["n_kept"] = 1
        return keep, meta

    sx = float(abs(step_x))
    sy = float(abs(step_y))
    if sx < 1.0 or sy < 1.0:
        keep = np.ones(n, dtype=bool)
        meta["n_kept"] = n
        return keep, meta

    if tol is not None:
        tx = ty = float(tol)
    else:
        pw = float(panel_width) if panel_width is not None and panel_width > 0 else sx
        ph = float(panel_height) if panel_height is not None and panel_height > 0 else sy
        # Isotropic: 10% of the largest side (panel or pitch) — better under warp
        side = float(max(pw, ph, sx, sy))
        t = float(tol_frac) * side
        tx = float(tol_x) if tol_x is not None else t
        ty = float(tol_y) if tol_y is not None else t
    tx = max(tx, 1.0)
    ty = max(ty, 1.0)
    meta["tol_x"] = tx
    meta["tol_y"] = ty
    meta["tol"] = float(max(tx, ty))  # compat / logs
    meta["tol_mode"] = "isotropic_max_side"

    tree = cKDTree(pts)
    steps = ((sx, 0.0), (-sx, 0.0), (0.0, sy), (0.0, -sy))

    # Directional neighbor flags for 2D seeds (horizontal AND vertical)
    has_h = np.zeros(n, dtype=bool)
    has_v = np.zeros(n, dtype=bool)
    degree = np.zeros(n, dtype=np.int32)
    for i in range(n):
        for dx, dy in steps:
            pred = pts[i] + np.array([dx, dy], dtype=np.float64)
            j = _nearest_in_tol_box(tree, pts, pred, tx, ty)
            if j is None or j == i:
                continue
            degree[i] += 1
            if abs(dx) > abs(dy):
                has_h[i] = True
            else:
                has_v[i] = True

    if require_2d_seed:
        seed_idxs = np.flatnonzero(has_h & has_v)
    else:
        seed_idxs = np.flatnonzero(degree >= 2)

    # Prefer denser cores first (multi-source queue)
    if len(seed_idxs) == 0:
        # No 2D core at this pitch → reject (do not keep FP mini-lattices /
        # rooftop blobs that only "fit" their own local spacing).
        keep = np.zeros(n, dtype=bool)
        meta["n_seeds"] = 0
        meta["n_kept"] = 0
        meta["no_2d_core"] = True
        return keep, meta

    seed_idxs = seed_idxs[np.argsort(-degree[seed_idxs])]
    meta["n_seeds"] = int(len(seed_idxs))
    meta["no_2d_core"] = False

    keep = np.zeros(n, dtype=bool)
    q: deque[int] = deque()
    for s in seed_idxs.tolist():
        if not keep[s]:
            keep[s] = True
            q.append(s)

    while q:
        i = q.popleft()
        pi = pts[i]
        for dx, dy in steps:
            pred = pi + np.array([dx, dy], dtype=np.float64)
            j = _nearest_in_tol_box(tree, pts, pred, tx, ty)
            if j is None or keep[j]:
                continue
            keep[j] = True
            q.append(j)

    meta["n_kept"] = int(keep.sum())
    return keep, meta


def _grid_params_from_kept(
    points: np.ndarray,
    keep: np.ndarray,
    step_x: float,
    step_y: float,
) -> dict[str, Any] | None:
    """LS pitch/origin on kept points for border/fill only."""
    pts = np.asarray(points, dtype=np.float64)
    kept_pts = pts[keep]
    if len(kept_pts) < 3:
        if len(kept_pts) == 0:
            return None
        c0 = kept_pts[0]
        return {
            "translation_x": float(-c0[0]),
            "translation_y": float(-c0[1]),
            "delta_x": float(step_x),
            "delta_y": float(step_y),
            "jitter_x": 0.0,
            "jitter_y": 0.0,
            "fit_pass": "seed_only",
        }

    # Seed params: origin so first kept point is near (0,0)
    c0 = kept_pts[0]
    seed = {
        "translation_x": float(-c0[0]),
        "translation_y": float(-c0[1]),
        "delta_x": float(step_x),
        "delta_y": float(step_y),
        "jitter_x": 0.0,
        "jitter_y": 0.0,
    }
    outlier_threshold = 0.15 * min(step_x, step_y)
    _mask, params = refit_grid_least_squares(
        kept_pts,
        seed,
        max(outlier_threshold, 5.0),
        inlier_mask=None,
    )
    params["fit_pass"] = "walk_ls"
    params["walk_kept"] = int(keep.sum())
    return params


def _process_one_cluster(
    cluster_pts: np.ndarray,
    template_width: float,
    template_height: float,
    *,
    global_dx: float,
    global_dy: float,
    pitch_slack: float = 0.05,
    walk_tol: float | None = None,
    walk_tol_frac: float = 0.10,
) -> tuple[np.ndarray, dict[str, Any] | None, dict[str, Any]]:
    """
    Walk with near-global pitch (local refine clamped to ±pitch_slack).
    """
    tw, th = float(template_width), float(template_height)
    gdx, gdy = float(global_dx), float(global_dy)
    local_dx, local_dy, pitch_meta = estimate_pitch_from_neighbors(cluster_pts, gdx, gdy)
    slack = float(max(0.0, pitch_slack))
    if pitch_meta.get("used_neighbors") and gdx > 1 and gdy > 1:
        seed_dx = float(np.clip(local_dx, gdx * (1.0 - slack), gdx * (1.0 + slack)))
        seed_dy = float(np.clip(local_dy, gdy * (1.0 - slack), gdy * (1.0 + slack)))
    else:
        seed_dx, seed_dy = gdx, gdy

    keep, walk_meta = local_lattice_walk(
        cluster_pts,
        seed_dx,
        seed_dy,
        panel_width=tw,
        panel_height=th,
        tol_frac=walk_tol_frac,
        tol=walk_tol,
        require_2d_seed=True,
    )
    walk_meta["pitch_from_neighbors"] = bool(pitch_meta.get("used_neighbors"))
    walk_meta["pitch_seed_dx"] = float(seed_dx)
    walk_meta["pitch_seed_dy"] = float(seed_dy)
    walk_meta["global_dx"] = gdx
    walk_meta["global_dy"] = gdy
    params = _grid_params_from_kept(cluster_pts, keep, seed_dx, seed_dy)
    if params is not None:
        params["pitch_from_neighbors"] = walk_meta["pitch_from_neighbors"]
        params["pitch_seed_dx"] = seed_dx
        params["pitch_seed_dy"] = seed_dy
        params["n_seeds"] = walk_meta["n_seeds"]
        params["walk_tol"] = walk_meta["tol"]
        params["walk_tol_x"] = walk_meta["tol_x"]
        params["walk_tol_y"] = walk_meta["tol_y"]
    return keep, params, walk_meta


def _keep_reject(conf: float, threshold: float, *, keep_high_conf_outliers: bool) -> bool:
    if not keep_high_conf_outliers:
        return False
    return conf >= threshold


def advanced_grid_validation_bruteforce(
    detections: list[dict[str, Any]],
    template_width: float,
    template_height: float,
    *,
    eps_pixels: float | None = None,
    min_samples: int = 4,
    min_cluster_size: int = 12,
    delta_jitter: float = 0.03,
    fine_tuning_confidence_threshold: float = 0.65,
    n_translations: int = 2000,
    keep_high_conf_outliers: bool = False,
    use_neighbor_pitch: bool = True,
    two_pass_fit: bool = True,
    walk_tol: float | None = None,
    walk_tol_frac: float = 0.10,
    pitch_slack: float = 0.05,
    parallel_clusters: bool = True,
) -> tuple[list[dict[str, Any]], dict[int, dict | None], dict[int, list], dict[str, int]]:
    """
    DBSCAN → local lattice walk (keep) → LS grid params on kept (for fill/border).

    ``min_samples`` is DBSCAN core density (≈4 for a pitch-eps lattice).
    ``min_cluster_size`` drops clusters smaller than this after DBSCAN.
    """
    _ = (delta_jitter, n_translations, two_pass_fit, use_neighbor_pitch)
    min_cluster_size = int(max(1, min_cluster_size))
    min_samples = int(max(2, min_samples))
    empty_stats = {
        "noise": 0,
        "noise_dropped": 0,
        "noise_kept_high_conf": 0,
        "grid_inliers": 0,
        "grid_outliers_dropped": 0,
        "grid_outliers_kept_high_conf": 0,
        "tiny_cluster_dropped": 0,
        "tiny_cluster_kept_high_conf": 0,
        "no_fit_dropped": 0,
        "no_fit_kept_high_conf": 0,
        "two_pass_ls": 0,
        "neighbor_pitch": 0,
        "walk_clusters": 0,
        "walk_seeds": 0,
        "min_cluster_size": min_cluster_size,
        "dbscan_min_samples": min_samples,
    }
    if len(detections) < min_samples:
        out = []
        for det in detections:
            d = dict(det)
            d["is_grid_aligned"] = True
            d["cluster_id"] = -1
            d["fate"] = "kept"
            out.append(d)
        return out, {}, {}, {**empty_stats, "grid_inliers": len(out)}

    tw = float(template_width)
    th = float(template_height)
    centers = []
    for det in detections:
        x, y, w, h = det["bbox_pixels"]
        centers.append([x + w / 2.0, y + h / 2.0])
    centers_arr = np.asarray(centers, dtype=np.float64)

    # One global pitch / eps for the whole ortho (±5% slack later for walk only).
    global_dx, global_dy, pitch_meta = estimate_pitch_from_neighbors(centers_arr, tw, th)
    if not pitch_meta.get("used_neighbors"):
        global_dx, global_dy = float(tw), float(th)
    # Connect one pitch with a small margin; single value for all DBSCAN.
    if eps_pixels is None:
        eps_pixels = float(max(global_dx, global_dy) * 1.15)
        eps_pixels = float(max(eps_pixels, max(tw, th) * 1.25))

    labels = DBSCAN(eps=float(eps_pixels), min_samples=int(min_samples)).fit_predict(centers_arr)
    validated: list[dict[str, Any]] = []
    for i, det in enumerate(detections):
        d = dict(det)
        d["cluster_id"] = int(labels[i])
        d["is_grid_aligned"] = False
        validated.append(d)

    stats = dict(empty_stats)
    stats["dbscan_eps"] = float(eps_pixels)
    stats["global_dx"] = float(global_dx)
    stats["global_dy"] = float(global_dy)

    for i, det in enumerate(validated):
        if det["cluster_id"] == -1:
            stats["noise"] += 1
            conf = float(det.get("confidence") or 0.0)
            if _keep_reject(conf, fine_tuning_confidence_threshold, keep_high_conf_outliers=keep_high_conf_outliers):
                det["is_grid_aligned"] = True
                det["fate"] = "kept"
                stats["noise_kept_high_conf"] += 1
            else:
                det["is_grid_aligned"] = False
                det["fate"] = "dbscan_noise"
                stats["noise_dropped"] += 1

    cluster_grid_fits: dict[int, dict | None] = {}
    removed_by_cluster: dict[int, list] = {}
    valid_clusters = [int(c) for c in np.unique(labels) if c != -1]

    # Build work list
    work: list[tuple[int, np.ndarray, np.ndarray]] = []
    for cid in valid_clusters:
        idxs = np.where(labels == cid)[0]
        if len(idxs) < min_cluster_size:
            removed = []
            for j in idxs:
                conf = float(validated[j].get("confidence") or 0.0)
                if _keep_reject(
                    conf, fine_tuning_confidence_threshold, keep_high_conf_outliers=keep_high_conf_outliers
                ):
                    validated[j]["is_grid_aligned"] = True
                    validated[j]["fate"] = "kept"
                    stats["tiny_cluster_kept_high_conf"] += 1
                else:
                    validated[j]["is_grid_aligned"] = False
                    validated[j]["fate"] = "tiny_cluster"
                    removed.append(validated[j])
                    stats["tiny_cluster_dropped"] += 1
            removed_by_cluster[cid] = removed
            continue
        work.append((cid, idxs, centers_arr[idxs]))

    def _apply_result(
        cid: int,
        idxs: np.ndarray,
        keep: np.ndarray,
        params: dict[str, Any] | None,
        walk_meta: dict[str, Any],
    ) -> None:
        stats["walk_clusters"] += 1
        stats["walk_seeds"] += int(walk_meta.get("n_seeds") or 0)
        if walk_meta.get("pitch_from_neighbors"):
            stats["neighbor_pitch"] += 1
        if params is not None and params.get("fit_pass") == "walk_ls":
            stats["two_pass_ls"] += 1

        removed: list = []
        if params is None or int(keep.sum()) == 0:
            cluster_grid_fits[cid] = None
            for j in idxs:
                conf = float(validated[j].get("confidence") or 0.0)
                if _keep_reject(
                    conf, fine_tuning_confidence_threshold, keep_high_conf_outliers=keep_high_conf_outliers
                ):
                    validated[j]["is_grid_aligned"] = True
                    validated[j]["fate"] = "kept"
                    stats["no_fit_kept_high_conf"] += 1
                else:
                    validated[j]["is_grid_aligned"] = False
                    validated[j]["fate"] = "no_fit"
                    removed.append(validated[j])
                    stats["no_fit_dropped"] += 1
            removed_by_cluster[cid] = removed
            return

        n_in = int(keep.sum())
        cluster_grid_fits[cid] = {
            **params,
            "n_inliers": n_in,
            "n_outliers": int(len(keep) - n_in),
            "inlier_rate": float(100.0 * n_in / max(1, len(keep))),
            "cluster_indices": idxs.tolist(),
        }
        for k, j in enumerate(idxs):
            if keep[k]:
                validated[j]["is_grid_aligned"] = True
                validated[j]["fate"] = "kept"
                stats["grid_inliers"] += 1
            else:
                conf = float(validated[j].get("confidence") or 0.0)
                if _keep_reject(
                    conf,
                    fine_tuning_confidence_threshold,
                    keep_high_conf_outliers=keep_high_conf_outliers,
                ):
                    validated[j]["is_grid_aligned"] = True
                    validated[j]["fate"] = "kept"
                    stats["grid_outliers_kept_high_conf"] += 1
                else:
                    validated[j]["is_grid_aligned"] = False
                    validated[j]["fate"] = "walk_reject"
                    removed.append(validated[j])
                    stats["grid_outliers_dropped"] += 1
        removed_by_cluster[cid] = removed

    if not work:
        return validated, cluster_grid_fits, removed_by_cluster, stats

    workers = 1
    if parallel_clusters and len(work) > 1:
        workers = max(1, min(len(work), max(1, (os.cpu_count() or 2) - 1)))

    if workers == 1:
        for cid, idxs, cluster_pts in work:
            keep, params, walk_meta = _process_one_cluster(
                cluster_pts,
                tw,
                th,
                global_dx=global_dx,
                global_dy=global_dy,
                pitch_slack=pitch_slack,
                walk_tol=walk_tol,
                walk_tol_frac=walk_tol_frac,
            )
            _apply_result(cid, idxs, keep, params, walk_meta)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(
                    _process_one_cluster,
                    cluster_pts,
                    tw,
                    th,
                    global_dx=global_dx,
                    global_dy=global_dy,
                    pitch_slack=pitch_slack,
                    walk_tol=walk_tol,
                    walk_tol_frac=walk_tol_frac,
                ): (cid, idxs)
                for cid, idxs, cluster_pts in work
            }
            for fut in as_completed(futs):
                cid, idxs = futs[fut]
                keep, params, walk_meta = fut.result()
                _apply_result(cid, idxs, keep, params, walk_meta)

    return validated, cluster_grid_fits, removed_by_cluster, stats


# --- Compat aliases used by older tests ---
def fit_grid_bruteforce(
    points: np.ndarray,
    delta_x: float,
    delta_y: float,
    outlier_threshold: float,
    n_translations: int = 2000,
    delta_jitter: float = 0.03,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray | None, dict[str, float] | None]:
    """Legacy brute-force lattice (kept for unit tests)."""
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


def fit_grid_two_pass(
    points: np.ndarray,
    template_width: float,
    template_height: float,
    outlier_threshold: float,
    *,
    n_translations: int = 2000,
    delta_jitter: float = 0.03,
    rng: np.random.Generator | None = None,
    use_neighbor_pitch: bool = True,
) -> tuple[np.ndarray | None, dict[str, Any] | None]:
    """Compat: neighbor pitch + local walk keep mask (+ LS params on kept)."""
    _ = (outlier_threshold, n_translations, delta_jitter, rng)
    keep, params, meta = _process_one_cluster(
        np.asarray(points, dtype=np.float64),
        float(template_width),
        float(template_height),
        global_dx=float(template_width),
        global_dy=float(template_height),
        walk_tol=None,
    )
    if params is None:
        return keep, None
    params = dict(params)
    params["pitch_from_neighbors"] = meta.get("pitch_from_neighbors")
    params["fit_pass"] = "walk"
    if not use_neighbor_pitch:
        # still walked with neighbor-or-template seed inside estimate
        pass
    return keep, params
