"""Tests for thermal fill colors and soft labels."""

from __future__ import annotations

from openpvscope.segmentation.thermal_color import (
    get_thermal_color_for_value,
    iou_fallback_color,
    percentile_range,
    soft_label,
)


def test_thermal_color_endpoints() -> None:
    assert get_thermal_color_for_value(0.0, 0.0, 10.0, {"min": 2.0, "max": 8.0}) == "#00FF00"
    assert get_thermal_color_for_value(10.0, 0.0, 10.0, {"min": 2.0, "max": 8.0}) == "#FF0000"
    mid = get_thermal_color_for_value(5.0, 0.0, 10.0, {"min": 0.0, "max": 10.0})
    assert mid.startswith("#")
    assert len(mid) == 7


def test_thermal_color_missing() -> None:
    assert get_thermal_color_for_value(None, 0.0, 1.0) == "#808080"


def test_iou_fallback() -> None:
    assert iou_fallback_color(0.9) == "lime"
    assert iou_fallback_color(0.4) == "yellow"
    assert iou_fallback_color(0.1) == "orange"


def test_soft_label() -> None:
    assert soft_label(1.0, 2.0, 8.0) == 0.0
    assert soft_label(9.0, 2.0, 8.0) == 1.0
    assert soft_label(5.0, 2.0, 8.0) == 0.5
    assert soft_label(None, 2.0, 8.0) is None


def test_percentile_range() -> None:
    vals = list(range(100))
    r = percentile_range([float(v) for v in vals])
    assert r["min"] < r["max"]
    assert 80 <= r["min"] <= 90
    assert 90 <= r["max"] <= 99
