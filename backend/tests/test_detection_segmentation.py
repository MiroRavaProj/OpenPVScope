"""Unit tests for detection grid, NMS, pairing, GeoJSON helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openpvscope.detection.grid import build_grid_cells, regularize_quad
from openpvscope.detection.pipeline import generate_grid, save_aoi_geojson
from openpvscope.detection.template_match import nms
from openpvscope.geo.crs import feature_collection, polygon_feature
from openpvscope.segmentation.pairing import (
    distance_meters,
    iou_from_centers,
    pair_panels_self,
    pair_rgb_thermal_panels,
)


def test_regularize_quad_keeps_side() -> None:
    """Width must stay on the same side of p0→p1 as the user's p3 (no flip)."""
    # p0--p1 along +x; p3/p2 below (-y)
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, -4.0), (0.0, -4.0)]
    rect = regularize_quad(pts)
    # All rect y should be <= 0 (not flipped above to +y)
    assert all(p[1] <= 0.01 for p in rect)
    assert min(p[1] for p in rect) == pytest.approx(-4.0, abs=0.05)


def test_build_grid_cells_count() -> None:
    rect = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (0.0, 4.0)]
    cells = build_grid_cells(rect, rows=2, cols=5)
    assert len(cells) == 10
    assert cells[0]["row"] == 0 and cells[0]["col"] == 0
    assert cells[-1]["row"] == 1 and cells[-1]["col"] == 4
    assert len(cells[0]["ring"]) == 4


def test_nms_suppresses_overlap() -> None:
    boxes = [(0, 0, 10, 10), (1, 1, 10, 10), (50, 50, 10, 10)]
    scores = [0.9, 0.8, 0.7]
    keep = nms(boxes, scores, 0.3)
    assert keep == [0, 2]


def test_pair_panels_self() -> None:
    fc = feature_collection(
        [
            polygon_feature([[0, 0], [1, 0], [1, 1], [0, 1]], {"confidence": 0.8}, fid="a"),
            polygon_feature([[2, 2], [3, 2], [3, 3], [2, 3]], {"confidence": 0.7}, fid="b"),
        ]
    )
    pairs = pair_panels_self(fc)
    assert len(pairs) == 2
    assert {p["id"] for p in pairs} == {"a", "b"}
    assert pairs[0]["iou"] == 1.0


def test_pair_rgb_thermal_geographic() -> None:
    # ~1 m panels near 45°N; thermal slightly offset but within radius
    rgb = feature_collection(
        [
            polygon_feature(
                [[12.0, 45.0], [12.00002, 45.0], [12.00002, 45.00002], [12.0, 45.00002]],
                {"confidence": 0.9, "id": "rgb1"},
                fid="rgb1",
            ),
            polygon_feature(
                [[12.001, 45.0], [12.00102, 45.0], [12.00102, 45.00002], [12.001, 45.00002]],
                {"confidence": 0.85, "id": "rgb2"},
                fid="rgb2",
            ),
        ]
    )
    th = feature_collection(
        [
            polygon_feature(
                [
                    [12.000005, 45.000005],
                    [12.000025, 45.000005],
                    [12.000025, 45.000025],
                    [12.000005, 45.000025],
                ],
                {"confidence": 0.8, "id": "th1"},
                fid="th1",
            ),
            polygon_feature(
                [
                    [12.001005, 45.000005],
                    [12.001025, 45.000005],
                    [12.001025, 45.000025],
                    [12.001005, 45.000025],
                ],
                {"confidence": 0.75, "id": "th2"},
                fid="th2",
            ),
        ]
    )
    pairs = pair_rgb_thermal_panels(rgb, th, search_radius_m=8.0, min_iou=0.05)
    assert len(pairs) == 2
    assert {p["rgb_id"] for p in pairs} == {"rgb1", "rgb2"}
    assert {p["thermal_id"] for p in pairs} == {"th1", "th2"}
    assert all(p["thermal_ring"] for p in pairs)
    assert all(p["iou"] >= 0.05 for p in pairs)


def test_iou_and_distance_helpers() -> None:
    assert iou_from_centers(12.0, 45.0, 12.0, 45.0) == pytest.approx(1.0)
    assert distance_meters(12.0, 45.0, 12.0, 45.0) == pytest.approx(0.0)
    assert distance_meters(12.0, 45.0, 12.0001, 45.0) > 5.0


def test_aoi_grid_geojson_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "detection" / "rgb").mkdir(parents=True)
    (root / "detection" / "thermal").mkdir(parents=True)
    ring = [[12.0, 42.0], [12.001, 42.0], [12.001, 42.001], [12.0, 42.001]]
    save_aoi_geojson(root, ring)
    aoi_path = root / "detection" / "rgb" / "aoi.geojson"
    assert aoi_path.is_file()
    data = json.loads(aoi_path.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1

    result = generate_grid(root, rows=2, cols=3)
    assert result["cell_count"] == 6
    grid = json.loads((root / "detection" / "rgb" / "grid.geojson").read_text(encoding="utf-8"))
    assert len(grid["features"]) == 6


def test_copy_rgb_to_thermal_and_edit_aoi(tmp_path: Path) -> None:
    from openpvscope.detection.pipeline import copy_rgb_grid_to_thermal, detection_status

    root = tmp_path / "proj"
    (root / "detection" / "rgb").mkdir(parents=True)
    (root / "detection" / "thermal").mkdir(parents=True)
    ring = [[12.0, 42.0], [12.001, 42.0], [12.001, 42.001], [12.0, 42.001]]
    save_aoi_geojson(root, ring, modality="rgb")
    generate_grid(root, rows=2, cols=3, modality="rgb")
    copy_rgb_grid_to_thermal(root)
    assert (root / "detection" / "thermal" / "grid.geojson").is_file()

    edited = [[12.0, 42.0], [12.002, 42.0], [12.002, 42.002], [12.0, 42.002]]
    save_aoi_geojson(root, edited, modality="thermal", regenerate_grid=True)
    th_grid = json.loads((root / "detection" / "thermal" / "grid.geojson").read_text(encoding="utf-8"))
    assert len(th_grid["features"]) == 6
    st = detection_status(root)
    assert st["rgb"]["has_grid"]
    assert st["thermal"]["has_grid"]
    assert st["thermal"]["has_aoi"]


def test_deskew_angle_sign() -> None:
    from openpvscope.detection.deskew import aoi_deskew_angle_deg

    # Long side nearly east (90°) → little rotation toward 90
    ring = [[0.0, 0.0], [0.01, 0.001], [0.009, 0.005], [-0.001, 0.004]]
    angle = aoi_deskew_angle_deg(ring)
    assert abs(angle) < 45.0


def test_deskew_prefers_pixel_longest_side() -> None:
    """With a non-square pixel size, geo-degree longest side can disagree; affine wins."""
    from rasterio.transform import from_origin

    from openpvscope.detection.deskew import aoi_deskew_angle_deg

    # 1m x 10m pixels — vertical geo side can be short in degrees but long in pixels
    affine = from_origin(12.0, 42.01, 0.00001, 0.0001)
    ring = [[12.0, 42.0], [12.001, 42.0], [12.001, 42.002], [12.0, 42.002]]
    angle = aoi_deskew_angle_deg(ring, affine=affine)
    assert isinstance(angle, float)


def test_oriented_quads_from_seed() -> None:
    from openpvscope.detection.deskew import oriented_quads_from_seed

    seed = [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]]
    # centroid at (1, 0.5)
    quads = oriented_quads_from_seed(seed, [(11.0, 10.5)])
    assert len(quads) == 1
    assert quads[0][0] == pytest.approx([10.0, 10.0])
    assert quads[0][2] == pytest.approx([12.0, 11.0])


def test_polygon_feature_closes_ring() -> None:
    feat = polygon_feature([[0, 0], [1, 0], [1, 1], [0, 1]], {"kind": "t"}, fid="x")
    coords = feat["geometry"]["coordinates"][0]
    assert coords[0] == coords[-1]
    assert feat["properties"]["id"] == "x"
