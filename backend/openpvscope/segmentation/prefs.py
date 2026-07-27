"""Project-wise segmentation UI / run parameters."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from openpvscope.segmentation.pairing import DEFAULT_MIN_IOU

DEFAULT_SEGMENTATION_PARAMS: dict[str, Any] = {
    "margin_factor": 0.2,
    "min_iou": DEFAULT_MIN_IOU,
    "search_radius_m": None,
}


def params_path(project_root: Path) -> Path:
    return Path(project_root) / "segmentation" / "params.json"


def default_segmentation_params() -> dict[str, Any]:
    return deepcopy(DEFAULT_SEGMENTATION_PARAMS)


def _merge(raw: Any) -> dict[str, Any]:
    out = default_segmentation_params()
    if not isinstance(raw, dict):
        return out
    if "margin_factor" in raw:
        try:
            out["margin_factor"] = float(raw["margin_factor"])
        except (TypeError, ValueError):
            pass
    if "min_iou" in raw:
        try:
            out["min_iou"] = float(raw["min_iou"])
        except (TypeError, ValueError):
            pass
    if "search_radius_m" in raw:
        val = raw["search_radius_m"]
        if val is None:
            out["search_radius_m"] = None
        else:
            try:
                out["search_radius_m"] = float(val)
            except (TypeError, ValueError):
                pass
    return out


def load_segmentation_params(project_root: Path) -> dict[str, Any]:
    path = params_path(project_root)
    if not path.is_file():
        return default_segmentation_params()
    try:
        return _merge(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return default_segmentation_params()


def save_segmentation_params(project_root: Path, data: Any) -> dict[str, Any]:
    params = _merge(data)
    path = params_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    return params
