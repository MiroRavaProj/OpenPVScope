"""Tests for panel fate tagging and selection rewrite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openpvscope.detection.panel_selection import (
    apply_panel_selection,
    write_panels_from_features,
)
from openpvscope.detection.refine import run_advanced_validation
from openpvscope.geo.crs import polygon_feature


def test_min_cluster_size_does_not_break_lattice_dbscan() -> None:
    """min_cluster_size=12 must not be used as DBSCAN min_samples (lattice has ~4 neighbors)."""
    dets = []
    for i in range(10):
        for j in range(5):
            dets.append({"bbox": [100 + i * 56.0, 200 + j * 29.0, 50, 26], "confidence": 0.9})
    kept, stats = run_advanced_validation(
        dets,
        50.0,
        26.0,
        min_samples=4,
        min_cluster_size=12,
        keep_high_conf_outliers=False,
    )
    assert stats["grid_stats"].get("dbscan_min_samples") == 4
    assert stats["grid_stats"].get("min_cluster_size") == 12
    assert len(kept) >= 40
    assert stats["grid_stats"]["noise_dropped"] < 10


def test_refine_tags_fates_and_all_detections() -> None:
    dets = []
    for i in range(5):
        for j in range(5):
            dets.append({"bbox": [100 + i * 40, 100 + j * 30, 40, 30], "confidence": 0.9})
    # Off-grid noise
    for k in range(6):
        dets.append({"bbox": [800, 100 + k * 30, 40, 30], "confidence": 0.4})

    kept, stats = run_advanced_validation(
        dets,
        40.0,
        30.0,
        fine_tuning_confidence_threshold=0.65,
        n_translations=800,
        min_samples=4,
        keep_high_conf_outliers=False,
    )
    audit = stats["all_detections"]
    assert len(audit) >= len(kept)
    fates = {d.get("fate") for d in audit}
    assert "kept" in fates or any(d.get("include") for d in audit)
    assert any(not d.get("include") for d in audit)
    assert all("fate" in d for d in audit)
    assert all("include" in d for d in audit)
    included = [d for d in audit if d.get("include")]
    assert len(included) == len(kept)


def test_border_peel_removes_isolated_spur() -> None:
    from openpvscope.detection.refine_border import remove_border_outliers_with_fitted_grids

    dets = []
    for i in range(6):
        for j in range(3):
            dets.append(
                {
                    "bbox_pixels": [100 + i * 56.0, 200 + j * 29.0, 50, 26],
                    "confidence": 0.9,
                    "is_grid_aligned": True,
                    "cluster_id": 0,
                }
            )
    # Isolated spur one pitch above column 2
    dets.append(
        {
            "bbox_pixels": [100 + 2 * 56.0, 200 - 29.0, 50, 26],
            "confidence": 0.85,
            "is_grid_aligned": True,
            "cluster_id": 0,
        }
    )
    fits = {
        0: {
            "delta_x": 56.0,
            "delta_y": 29.0,
            "translation_x": -100.0,
            "translation_y": -200.0,
            "pitch_seed_dx": 56.0,
            "pitch_seed_dy": 29.0,
        }
    }
    enhanced, removed = remove_border_outliers_with_fitted_grids(
        dets, fits, keep_high_conf_outliers=False
    )
    spur = enhanced[-1]
    assert spur.get("border_outlier") is True
    assert spur.get("fate") == "border_prune"
    assert any(d.get("border_outlier") for d in removed)


def test_border_does_not_cascade_peel_full_row() -> None:
    """A full row with only horizontal neighbors must keep interiors (no iterative wipe)."""
    from openpvscope.detection.refine_border import remove_border_outliers_with_fitted_grids

    dets = []
    # Two rows; simulate missing vertical link by using huge step_y in fits so
    # only horizontal neighbors count — interiors still have L+R = 2.
    for i in range(10):
        for j in range(2):
            dets.append(
                {
                    "bbox_pixels": [100 + i * 56.0, 200 + j * 29.0, 50, 26],
                    "confidence": 0.9,
                    "is_grid_aligned": True,
                    "cluster_id": 0,
                }
            )
    fits = {
        0: {
            "delta_x": 56.0,
            "delta_y": 29.0,
            "translation_x": -100.0,
            "translation_y": -200.0,
            "pitch_seed_dx": 56.0,
            "pitch_seed_dy": 200.0,  # break vertical matching
        }
    }
    enhanced, removed = remove_border_outliers_with_fitted_grids(
        dets, fits, keep_high_conf_outliers=False
    )
    kept = [d for d in enhanced if not d.get("border_outlier")]
    # Ends may drop; should not wipe the row (10+ interiors across 2 rows)
    assert len(kept) >= 12
    assert len(removed) <= 8


def test_spatial_fill_restores_interior_gap() -> None:
    from openpvscope.detection.refine_fill import fill_missing_panels_conway_style

    dets = []
    removed = []
    for i in range(5):
        for j in range(3):
            det = {
                "bbox_pixels": [100 + i * 56.0, 200 + j * 29.0, 50, 26],
                "confidence": 0.9,
                "is_main_grid": True,
                "is_grid_aligned": True,
                "cluster_id": 0,
            }
            if i == 2 and j == 1:
                removed.append({**det, "is_main_grid": False, "confidence": 0.88})
                continue
            dets.append(det)
    fits = {
        0: {
            "delta_x": 56.0,
            "delta_y": 29.0,
            "translation_x": -100.0,
            "translation_y": -200.0,
            "pitch_seed_dx": 56.0,
            "pitch_seed_dy": 29.0,
        }
    }
    out, new_panels = fill_missing_panels_conway_style(
        dets,
        fits,
        removed_by_cluster={0: removed},
        fine_tuning_confidence_threshold=0.65,
    )
    assert len(new_panels) >= 1
    assert any(p.get("fate") in ("filled_restored", "filled_synth") for p in new_panels)


def test_border_prune_keeps_interior_with_spatial_neighbors() -> None:
    """Interior cells must not be border-pruned when pitch neighbors exist (even if LS indices drift)."""
    from openpvscope.detection.refine_border import remove_border_outliers_with_fitted_grids

    dets = []
    for i in range(8):
        for j in range(4):
            dets.append(
                {
                    "bbox": [100 + i * 56.0, 200 + j * 29.0, 50, 26],
                    "bbox_pixels": [100 + i * 56.0, 200 + j * 29.0, 50, 26],
                    "confidence": 0.9,
                    "is_grid_aligned": True,
                    "cluster_id": 0,
                }
            )
    # Intentionally wrong LS delta (would break index-based neighbor counts)
    fits = {
        0: {
            "delta_x": 70.0,
            "delta_y": 40.0,
            "translation_x": -100.0,
            "translation_y": -200.0,
            "pitch_seed_dx": 56.0,
            "pitch_seed_dy": 29.0,
        }
    }
    enhanced, removed = remove_border_outliers_with_fitted_grids(
        dets, fits, keep_high_conf_outliers=False
    )
    # Corners may prune; interiors with 2+ cardinal neighbors must stay
    interior = [
        d
        for d in enhanced
        if not d.get("border_outlier")
        and 1 <= (d["bbox_pixels"][0] - 100) / 56 <= 6
        and 1 <= (d["bbox_pixels"][1] - 200) / 29 <= 2
    ]
    assert len(interior) >= 8
    assert len(removed) < len(dets) // 2


def test_dbscan_connects_row_when_pitch_exceeds_panel_size() -> None:
    """Panel bbox 40 with pitch 56 must still form one cluster (not noise)."""
    dets = []
    for i in range(8):
        for j in range(3):
            dets.append({"bbox": [100 + i * 56.0, 200 + j * 29.0, 40, 28], "confidence": 0.9})
    kept, stats = run_advanced_validation(
        dets,
        40.0,
        28.0,
        fine_tuning_confidence_threshold=0.65,
        min_samples=4,
        keep_high_conf_outliers=False,
    )
    audit = stats["all_detections"]
    noise = [d for d in audit if d.get("fate") == "dbscan_noise"]
    assert len(noise) == 0
    assert stats["grid_stats"]["noise_dropped"] == 0
    assert len(kept) >= 20


def test_panel_selection_rewrites_panels(tmp_path: Path) -> None:
    # Minimal project layout: detection/thermal/
    root = tmp_path
    det = root / "detection" / "thermal"
    det.mkdir(parents=True)
    feats = []
    for i, (fate, include) in enumerate(
        [("kept", True), ("walk_reject", False), ("border_prune", False)]
    ):
        pid = f"p{i:03d}"
        ring = [[i, 0], [i + 0.01, 0], [i + 0.01, 0.01], [i, 0.01], [i, 0]]
        feats.append(
            polygon_feature(
                ring,
                {
                    "kind": "panel",
                    "id": pid,
                    "modality": "thermal",
                    "confidence": 0.8,
                    "fate": fate,
                    "include": include,
                },
                fid=pid,
            )
        )
    counts = write_panels_from_features(root, "thermal", feats)
    assert counts["included"] == 1
    assert counts["total"] == 3

    panels = json.loads((det / "panels.geojson").read_text(encoding="utf-8"))
    assert len(panels["features"]) == 1

    result = apply_panel_selection(
        root, "thermal", set_fate={"fate": "walk_reject", "include": True}
    )
    assert result["included"] == 2
    panels2 = json.loads((det / "panels.geojson").read_text(encoding="utf-8"))
    assert len(panels2["features"]) == 2

    result2 = apply_panel_selection(root, "thermal", reset_defaults=True)
    assert result2["included"] == 1
