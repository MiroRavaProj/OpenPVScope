"""Photogrammetry setup (wizard answers + ODX options + product toggles)."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

Modalities = Literal["rgb_and_thermal", "thermal_only"]
PhotoMode = Literal["process", "skip"]

DEFAULT_ODX_OPTIONS: dict[str, Any] = {
    "orthophoto_resolution": 2.0,
    "feature_quality": "high",
    "pc_quality": "medium",
    "fast_orthophoto": False,
    "crop": 3.0,
}

DEFAULT_PRODUCTS: dict[str, bool] = {
    "ortho": True,
    "dense_pc": False,
    "sparse_pc": False,
    "dsm": False,
    "dtm": False,
}

DEFAULT_SETUP: dict[str, Any] = {
    "wizard_complete": False,
    "modalities": "rgb_and_thermal",
    "mode": "process",
    "odx": dict(DEFAULT_ODX_OPTIONS),
    "products": dict(DEFAULT_PRODUCTS),
}


def setup_path(project_root: Path) -> Path:
    return Path(project_root) / "photogrammetry" / "setup.json"


def default_setup() -> dict[str, Any]:
    return deepcopy(DEFAULT_SETUP)


def _merge_odx(raw: Any) -> dict[str, Any]:
    out = dict(DEFAULT_ODX_OPTIONS)
    if isinstance(raw, dict):
        if "orthophoto_resolution" in raw:
            try:
                out["orthophoto_resolution"] = float(raw["orthophoto_resolution"])
            except (TypeError, ValueError):
                pass
        fq = raw.get("feature_quality")
        if fq in ("ultra", "high", "medium", "low", "lowest"):
            out["feature_quality"] = fq
        pq = raw.get("pc_quality")
        if pq in ("ultra", "high", "medium", "low", "lowest"):
            out["pc_quality"] = pq
        if "fast_orthophoto" in raw:
            out["fast_orthophoto"] = bool(raw["fast_orthophoto"])
        if "crop" in raw:
            try:
                out["crop"] = float(raw["crop"])
            except (TypeError, ValueError):
                pass
    return out


def _merge_products(raw: Any) -> dict[str, bool]:
    out = dict(DEFAULT_PRODUCTS)
    if isinstance(raw, dict):
        for k in out:
            if k in raw:
                out[k] = bool(raw[k])
    out["ortho"] = True  # always required
    return out


def normalize_setup(data: Any) -> dict[str, Any]:
    base = default_setup()
    if not isinstance(data, dict):
        return base
    if "wizard_complete" in data:
        base["wizard_complete"] = bool(data["wizard_complete"])
    mods = data.get("modalities")
    if mods in ("rgb_and_thermal", "thermal_only"):
        base["modalities"] = mods
    mode = data.get("mode")
    if mode in ("process", "skip"):
        base["mode"] = mode
    base["odx"] = _merge_odx(data.get("odx"))
    base["products"] = _merge_products(data.get("products"))
    return base


def load_setup(project_root: Path) -> dict[str, Any]:
    path = setup_path(project_root)
    if not path.is_file():
        return default_setup()
    try:
        return normalize_setup(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return default_setup()


def save_setup(project_root: Path, data: Any) -> dict[str, Any]:
    setup = normalize_setup(data)
    path = setup_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(setup, indent=2), encoding="utf-8")
    return setup


def build_odx_argv(odx: dict[str, Any] | None, products: dict[str, bool] | None) -> list[str]:
    """Build ODX CLI flags from curated options + product toggles."""
    opts = _merge_odx(odx)
    prods = _merge_products(products)
    args: list[str] = [
        "--orthophoto-resolution",
        str(opts["orthophoto_resolution"]),
        "--feature-quality",
        str(opts["feature_quality"]),
        "--pc-quality",
        str(opts["pc_quality"]),
        "--crop",
        str(opts["crop"]),
    ]
    if opts["fast_orthophoto"]:
        args.append("--fast-orthophoto")
    if prods.get("dsm"):
        args.append("--dsm")
    if prods.get("dtm"):
        args.append("--dtm")
    if prods.get("dense_pc"):
        args.append("--pc-las")
    return args


def list_exported_products(project_root: Path, modality: str) -> list[dict[str, Any]]:
    """List files under photogrammetry/{modality}/exports/ plus orthophoto."""
    root = Path(project_root)
    items: list[dict[str, Any]] = []
    ortho = root / "inputs" / "ortho" / f"{modality}.tif"
    if ortho.is_file():
        items.append(
            {
                "id": "ortho",
                "label": "Orthophoto",
                "path": str(ortho),
                "exists": True,
                "size": ortho.stat().st_size,
            }
        )
    exports = root / "photogrammetry" / modality / "exports"
    mapping = [
        ("dense_pc", "point_cloud.laz", "Dense point cloud"),
        ("dense_pc_las", "point_cloud.las", "Dense point cloud (LAS)"),
        ("sparse_pc", "sparse.ply", "Sparse point cloud"),
        ("dsm", "dsm.tif", "DSM"),
        ("dtm", "dtm.tif", "DTM"),
    ]
    for pid, name, label in mapping:
        p = exports / name
        if p.is_file():
            items.append(
                {
                    "id": pid,
                    "label": label,
                    "path": str(p),
                    "exists": True,
                    "size": p.stat().st_size,
                }
            )
    return items
