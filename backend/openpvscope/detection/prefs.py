"""Project-wise detection UI / run parameters."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from openpvscope.detection.pipeline import (
    DEFAULT_ADVANCED_VALIDATION,
    DEFAULT_CONFIDENCE,
    DEFAULT_DBSCAN_MIN_SAMPLES,
    DEFAULT_FILL_CONFIDENCE,
    DEFAULT_FINE_TUNING_CONFIDENCE,
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_NMS_IOU,
    DEFAULT_NUM_TEMPLATES,
    DEFAULT_PITCH_SLACK,
    DEFAULT_THERMAL_MATCH_MODE,
    DEFAULT_THERMAL_TEMP_CAP,
    DEFAULT_WALK_TOL_FRAC,
)

DEFAULT_DETECTION_PARAMS: dict[str, Any] = {
    "rows": 4,
    "cols": 10,
    "confidence_rgb": DEFAULT_CONFIDENCE,
    "confidence_thermal": DEFAULT_CONFIDENCE,
    "nms_iou": DEFAULT_NMS_IOU,
    "num_templates": DEFAULT_NUM_TEMPLATES,
    "thermal_temp_cap": DEFAULT_THERMAL_TEMP_CAP,
    "advanced_validation": DEFAULT_ADVANCED_VALIDATION,
    "fine_tuning_confidence": DEFAULT_FINE_TUNING_CONFIDENCE,
    "thermal_match_mode": DEFAULT_THERMAL_MATCH_MODE,
    "keep_high_conf_outliers": False,
    "min_cluster_size": DEFAULT_MIN_CLUSTER_SIZE,
    "dbscan_min_samples": DEFAULT_DBSCAN_MIN_SAMPLES,
    "walk_tol_frac": DEFAULT_WALK_TOL_FRAC,
    "pitch_slack": DEFAULT_PITCH_SLACK,
    "fill_confidence": DEFAULT_FILL_CONFIDENCE,
}


def params_path(project_root: Path) -> Path:
    return Path(project_root) / "detection" / "params.json"


def default_detection_params() -> dict[str, Any]:
    return deepcopy(DEFAULT_DETECTION_PARAMS)


def _merge(raw: Any) -> dict[str, Any]:
    out = default_detection_params()
    if not isinstance(raw, dict):
        return out
    for key, default in out.items():
        if key not in raw:
            continue
        val = raw[key]
        try:
            if isinstance(default, bool):
                out[key] = bool(val)
            elif isinstance(default, int) and not isinstance(default, bool):
                out[key] = int(val)
            elif isinstance(default, float):
                out[key] = float(val)
            elif isinstance(default, str):
                if key == "thermal_match_mode" and val in (
                    "default",
                    "context_15",
                    "gradient",
                ):
                    out[key] = val
            else:
                out[key] = val
        except (TypeError, ValueError):
            pass
    return out


def load_detection_params(project_root: Path) -> dict[str, Any]:
    path = params_path(project_root)
    if not path.is_file():
        return default_detection_params()
    try:
        return _merge(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return default_detection_params()


def save_detection_params(project_root: Path, data: Any) -> dict[str, Any]:
    params = _merge(data)
    path = params_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(params, indent=2), encoding="utf-8")
    return params
