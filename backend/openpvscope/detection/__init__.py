"""Panel detection — scaffold for template-matching pipeline (ported later from thesis utils)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def detection_status(project_root: Path) -> dict[str, Any]:
    root = Path(project_root)
    rgb = root / "detection" / "rgb" / "panels.geojson"
    thermal = root / "detection" / "thermal" / "panels.geojson"
    return {
        "ready": False,
        "message": "Detection pipeline scaffold — algorithms will be ported from the thesis suite.",
        "has_rgb_panels": rgb.is_file(),
        "has_thermal_panels": thermal.is_file(),
    }


def save_placeholder_aoi(project_root: Path, modality: str, geojson: dict) -> Path:
    out = Path(project_root) / "detection" / modality / "aoi.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    return out
