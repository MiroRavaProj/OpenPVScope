"""Tests for crop orientation helpers and advanced validation refine."""

from __future__ import annotations

import numpy as np
import pytest

from openpvscope.detection.refine import run_advanced_validation
from openpvscope.detection.refine_grid import fit_grid_bruteforce, get_grid_indices_from_params
from openpvscope.segmentation.extract import _order_box_points


def test_order_box_and_edge_lengths_prefer_landscape() -> None:
    """Simulated minAreaRect box with swapped (w,h) vs ordered edges."""
    # Landscape panel corners in pixel space (col, row)
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
    # 4x4 grid spacing 20
    pts = []
    for i in range(4):
        for j in range(4):
            pts.append([10 + i * 20 + rng.normal(0, 0.5), 10 + j * 20 + rng.normal(0, 0.5)])
    pts = np.asarray(pts, dtype=np.float64)
    # one outlier
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
    # weak outlier
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


def test_get_grid_indices_roundtrip() -> None:
    centers = np.asarray([[10.0, 20.0], [30.0, 20.0]], dtype=np.float64)
    params = {"delta_x": 20.0, "delta_y": 20.0, "translation_x": 0.0, "translation_y": 0.0}
    g = get_grid_indices_from_params(centers, params)
    assert g.shape == (2, 2)
    assert g[0, 0] == pytest.approx(0.5)
