"""Photogrammetry setup + ODX argv helpers."""

from __future__ import annotations

from pathlib import Path

from openpvscope.photogrammetry.setup import (
    build_odx_argv,
    list_exported_products,
    load_setup,
    normalize_setup,
    save_setup,
)


def test_normalize_forces_ortho():
    s = normalize_setup(
        {
            "wizard_complete": True,
            "modalities": "thermal_only",
            "mode": "skip",
            "products": {"ortho": False, "dsm": True},
        }
    )
    assert s["products"]["ortho"] is True
    assert s["products"]["dsm"] is True
    assert s["modalities"] == "thermal_only"
    assert s["mode"] == "skip"


def test_build_odx_argv_products():
    args = build_odx_argv(
        {"orthophoto_resolution": 5, "fast_orthophoto": True},
        {"dense_pc": True, "dsm": True, "dtm": True},
    )
    assert args.count("--orthophoto-resolution") == 1
    assert "5" in args or "5.0" in args
    assert "--fast-orthophoto" in args
    assert "--dsm" in args
    assert "--dtm" in args
    assert "--pc-las" in args


def test_save_load_setup(tmp_path: Path):
    saved = save_setup(
        tmp_path,
        {
            "wizard_complete": True,
            "modalities": "rgb_and_thermal",
            "mode": "process",
            "odx": {"orthophoto_resolution": 1.5},
            "products": {"sparse_pc": True},
        },
    )
    assert saved["odx"]["orthophoto_resolution"] == 1.5
    loaded = load_setup(tmp_path)
    assert loaded["wizard_complete"] is True
    assert loaded["products"]["sparse_pc"] is True
    assert (tmp_path / "photogrammetry" / "setup.json").is_file()


def test_list_exported_products_empty(tmp_path: Path):
    assert list_exported_products(tmp_path, "rgb") == []
