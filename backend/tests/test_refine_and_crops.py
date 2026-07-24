"""Tests for crop orientation helpers and advanced validation refine."""

from __future__ import annotations

import numpy as np
import pytest

from openpvscope.detection.refine import run_advanced_validation
from openpvscope.detection.refine_grid import (
    estimate_pitch_from_neighbors,
    fit_grid_bruteforce,
    get_grid_indices_from_params,
    local_lattice_walk,
)
from openpvscope.segmentation.extract import _order_box_points


def test_order_box_and_edge_lengths_prefer_landscape() -> None:
    """Simulated minAreaRect box with swapped (w,h) vs ordered edges."""
    pts = np.asarray([[10, 10], [50, 12], [48, 30], [8, 28]], dtype=np.float32)
    import cv2

    rect = cv2.minAreaRect(pts)
    box = _order_box_points(cv2.boxPoints(rect))
    top = float(np.linalg.norm(box[1] - box[0]))
    left = float(np.linalg.norm(box[3] - box[0]))
    if top < left:
        box = np.asarray([box[3], box[0], box[1], box[2]], dtype=np.float32)
        top, left = left, top
    assert top >= left
    assert top > 20


def test_fit_grid_bruteforce_recovers_regular_grid() -> None:
    rng = np.random.default_rng(0)
    pts = []
    for i in range(4):
        for j in range(4):
            pts.append([10 + i * 20 + rng.normal(0, 0.5), 10 + j * 20 + rng.normal(0, 0.5)])
    pts = np.asarray(pts, dtype=np.float64)
    pts = np.vstack([pts, [[200.0, 200.0]]])
    mask, params = fit_grid_bruteforce(
        pts, 20.0, 20.0, outlier_threshold=3.0, n_translations=800, delta_jitter=0.03, rng=rng
    )
    assert mask is not None and params is not None
    assert int(mask.sum()) >= 14
    assert not bool(mask[-1])


def test_run_advanced_validation_smoke() -> None:
    dets = []
    for i in range(5):
        for j in range(5):
            x, y = 100 + i * 40, 100 + j * 30
            dets.append({"bbox": [x, y, 40, 30], "confidence": 0.9})
    dets.append({"bbox": [500, 500, 40, 30], "confidence": 0.2})
    out, stats = run_advanced_validation(
        dets,
        40.0,
        30.0,
        fine_tuning_confidence_threshold=0.65,
        n_translations=500,
        min_samples=4,
    )
    assert stats["input"] == 26
    assert stats["after_step3"] >= 20
    assert all("bbox" in d and "bbox_pixels" in d for d in out)


def test_strict_refine_drops_high_conf_noise() -> None:
    """High-score off-grid peaks must be dropped when keep_high_conf_outliers=False."""
    dets = []
    for i in range(5):
        for j in range(5):
            dets.append({"bbox": [100 + i * 40, 100 + j * 30, 40, 30], "confidence": 0.9})
    for k in range(8):
        dets.append({"bbox": [800, 100 + k * 30, 40, 30], "confidence": 0.92})

    soft, soft_stats = run_advanced_validation(
        dets,
        40.0,
        30.0,
        fine_tuning_confidence_threshold=0.65,
        n_translations=800,
        min_samples=4,
        keep_high_conf_outliers=True,
    )
    strict, strict_stats = run_advanced_validation(
        dets,
        40.0,
        30.0,
        fine_tuning_confidence_threshold=0.65,
        n_translations=800,
        min_samples=4,
        keep_high_conf_outliers=False,
    )
    assert soft_stats["after_step1"] > strict_stats["after_step1"]
    assert strict_stats["after_step1"] <= 25 + 2
    assert strict_stats["grid_stats"]["noise_dropped"] + strict_stats["grid_stats"][
        "grid_outliers_dropped"
    ] >= 5
    assert len(strict) < len(soft)


def test_get_grid_indices_roundtrip() -> None:
    centers = np.asarray([[10.0, 20.0], [30.0, 20.0]], dtype=np.float64)
    params = {"delta_x": 20.0, "delta_y": 20.0, "translation_x": 0.0, "translation_y": 0.0}
    g = get_grid_indices_from_params(centers, params)
    assert g.shape == (2, 2)
    assert g[0, 0] == pytest.approx(0.5)


def test_estimate_pitch_from_neighbors() -> None:
    pts = []
    for i in range(8):
        for j in range(3):
            pts.append([100 + i * 56.0, 200 + j * 29.0])
    pts = np.asarray(pts, dtype=np.float64)
    dx, dy, meta = estimate_pitch_from_neighbors(pts, 60.0, 32.0)
    assert meta["used_neighbors"] is True
    assert dx == pytest.approx(56.0, abs=0.5)
    assert dy == pytest.approx(29.0, abs=0.5)


def test_local_walk_keeps_long_row_with_pitch_drift() -> None:
    """Slight cumulative spacing change — walk recenters each hop, keeps ends."""
    rng = np.random.default_rng(2)
    pts = []
    x = 50.0
    for i in range(40):
        # slow pitch drift: 56 → ~57 over the row
        pitch = 56.0 + i * 0.03
        for j in range(4):
            pts.append([x + rng.normal(0, 0.3), 80 + j * 29.0 + rng.normal(0, 0.3)])
        x += pitch
    pts = np.asarray(pts, dtype=np.float64)
    keep, meta = local_lattice_walk(
        pts, 56.0, 29.0, panel_width=56.0, panel_height=29.0, tol_frac=0.10
    )
    # Isotropic: 10% of max(56, 29) = 5.6
    assert meta["tol_x"] == pytest.approx(5.6)
    assert meta["tol_y"] == pytest.approx(5.6)
    assert meta["n_seeds"] >= 1
    assert int(keep.sum()) >= 150  # of 160


def test_local_walk_reaches_around_gap_without_double_step() -> None:
    """Missing one interior panel: other branches still reach both sides (no 2× jump)."""
    pts = []
    for i in range(10):
        for j in range(4):
            if i == 5 and j == 1:
                continue  # gap
            pts.append([100 + i * 56.0, 200 + j * 29.0])
    pts = np.asarray(pts, dtype=np.float64)
    keep, meta = local_lattice_walk(
        pts, 56.0, 29.0, panel_width=56.0, panel_height=29.0, tol_frac=0.10
    )
    assert int(keep.sum()) == len(pts)
    assert meta["n_seeds"] >= 1


def test_local_walk_no_2d_core_rejects_cluster() -> None:
    """Pure 1D strip / no 2D seed at global pitch → reject (not keep as FP lattice)."""
    pts = np.asarray([[0.0, k * 29.0] for k in range(12)], dtype=np.float64)
    keep, meta = local_lattice_walk(
        pts, 56.0, 29.0, panel_width=56.0, panel_height=29.0, require_2d_seed=True
    )
    assert meta["n_seeds"] == 0
    assert meta.get("no_2d_core") is True
    assert int(keep.sum()) == 0


def test_local_walk_prunes_off_lattice_from_2d_core() -> None:
    """FP points off the lattice stay unreachable from the 2D core (no 2× jumps)."""
    pts = []
    for i in range(6):
        for j in range(4):
            pts.append([100 + i * 56.0, 200 + j * 29.0])
    # Vertical FP strip ~half-pitch off to the side
    for k in range(8):
        pts.append([100 + 2.5 * 56.0, 200 + k * 29.0])
    pts = np.asarray(pts, dtype=np.float64)
    keep, meta = local_lattice_walk(
        pts, 56.0, 29.0, panel_width=56.0, panel_height=29.0, tol_frac=0.10
    )
    assert meta["n_seeds"] >= 1
    assert int(keep.sum()) == 24
    assert int((~keep).sum()) == 8
