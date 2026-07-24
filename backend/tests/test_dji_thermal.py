"""Tests for DJI thermal format detection and SDK probe."""

from __future__ import annotations

from pathlib import Path

import pytest

from openpvscope.thermal.dji import (
    ThermalFormat,
    convert_dji_thermal,
    detect_thermal_format,
    prepare_thermal_for_photogrammetry,
    probe_dji_sdk,
)


def test_detect_tiff_by_extension(tmp_path: Path) -> None:
    p = tmp_path / "a.tif"
    p.write_bytes(b"II*\x00")
    assert detect_thermal_format(p) == ThermalFormat.TIFF


def test_detect_jpeg_magic_as_dji(tmp_path: Path) -> None:
    p = tmp_path / "t.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert detect_thermal_format(p) == ThermalFormat.DJI_PROPRIETARY


def test_detect_non_jpeg_unknown(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    p.write_bytes(b"notajpeg")
    assert detect_thermal_format(p) == ThermalFormat.UNKNOWN


def test_prepare_skips_visible_light(tmp_path: Path) -> None:
    src = tmp_path / "DJI_0001_V.JPG"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert prepare_thermal_for_photogrammetry(src, tmp_path / "out") is None


def test_probe_dji_sdk_shape() -> None:
    info = probe_dji_sdk()
    assert isinstance(info, dict)
    assert "available" in info
    assert "path" in info
    assert "error" in info
    assert isinstance(info["available"], bool)
    if info["available"]:
        assert info["path"]
        assert info["error"] is None
    else:
        assert info["error"]


def test_convert_requires_sdk_or_skips_without_sample(tmp_path: Path) -> None:
    info = probe_dji_sdk()
    if not info["available"]:
        src = tmp_path / "t.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 40)
        with pytest.raises(RuntimeError, match="DJI Thermal SDK"):
            convert_dji_thermal(src, tmp_path / "out.tif")
        return

    # SDK present: conversion needs a real R-JPEG sample (not bundled in CI).
    assert info["available"] is True
    assert Path(info["path"]).is_dir()
