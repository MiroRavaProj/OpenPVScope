"""Unit tests for detection grid, NMS, pairing, GeoJSON helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openpvscope.detection.grid import build_grid_cells, regularize_quad
from openpvscope.detection.pipeline import generate_grid, save_aoi_geojson
from openpvscope.detection.template_match import nms
from openpvscope.geo.crs import feature_collection, polygon_feature
from openpvscope.segmentation.pairing import pair_panels_self


def test_regularize_quad_rectangle() -> None:
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (0.0, 4.0)]
    rect = regularize_quad(pts)
    assert len(rect) == 4
    # roughly same footprint
    xs = [p[0] for p in rect]
    ys = [p[1] for p in rect]
    assert max(xs) - min(xs) == pytest.approx(10.0, abs=0.01)
    assert max(ys) - min(ys) == pytest.approx(4.0, abs=0.01)


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


def test_aoi_grid_geojson_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "detection" / "rgb").mkdir(parents=True)
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


def test_polygon_feature_closes_ring() -> None:
    feat = polygon_feature([[0, 0], [1, 0], [1, 1], [0, 1]], {"kind": "t"}, fid="x")
    coords = feat["geometry"]["coordinates"][0]
    assert coords[0] == coords[-1]
    assert feat["properties"]["id"] == "x"
