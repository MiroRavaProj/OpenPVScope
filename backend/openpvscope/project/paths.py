from __future__ import annotations

from pathlib import Path

STAGE_DIRS = (
    "inputs/raw/rgb",
    "inputs/raw/thermal",
    "inputs/ortho",
    "photogrammetry",
    "alignment",
    "detection/rgb",
    "detection/thermal",
    "segmentation/panels",
    "labels",
    "models",
    "classification",
    "exports",
    "work/overlays",
)


def ensure_project_tree(root: Path) -> None:
    for rel in STAGE_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)


def ortho_rgb(root: Path) -> Path:
    return root / "inputs" / "ortho" / "rgb.tif"


def ortho_thermal(root: Path) -> Path:
    return root / "inputs" / "ortho" / "thermal.tif"


def ortho_thermal_aligned(root: Path) -> Path:
    return root / "inputs" / "ortho" / "thermal_aligned.tif"
