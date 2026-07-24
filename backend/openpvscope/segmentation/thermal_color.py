"""Legacy thermal fill colors + soft labels (segmentation map / Save Labels)."""

from __future__ import annotations

from typing import Any

THERMAL_INDICATORS = (
    "max_temperature",
    "min_temperature",
    "mean_temperature",
    "median_temperature",
    "std_temperature",
    "var_temperature",
)

LABEL_INDICATORS = (
    "max_temperature",
    "mean_temperature",
    "median_temperature",
    "std_temperature",
)

GRAY = "#808080"


def get_thermal_color_for_value(
    value: float | None,
    min_val: float,
    max_val: float,
    color_range: dict[str, float] | None = None,
) -> str:
    """Green→red hex; clamp outside color_range (legacy get_thermal_color_for_value)."""
    if value is None:
        return GRAY
    try:
        v = float(value)
    except (TypeError, ValueError):
        return GRAY
    if max_val <= min_val:
        return "#00FF00"

    if color_range and isinstance(color_range, dict):
        effective_min = float(color_range.get("min", min_val))
        effective_max = float(color_range.get("max", max_val))
        if effective_max <= effective_min:
            return "#00FF00"
        if v <= effective_min:
            return "#00FF00"
        if v >= effective_max:
            return "#FF0000"
        normalized = (v - effective_min) / (effective_max - effective_min)
    else:
        normalized = (v - min_val) / (max_val - min_val)

    normalized = max(0.0, min(1.0, float(normalized)))
    red = int(normalized * 255)
    green = int((1.0 - normalized) * 255)
    return f"#{red:02x}{green:02x}00"


def iou_fallback_color(iou: float | None) -> str:
    """Legacy live path: lime / yellow / orange."""
    v = float(iou or 0.0)
    if v > 0.5:
        return "lime"
    if v > 0.3:
        return "yellow"
    return "orange"


def soft_label(value: float | None, green: float, red: float) -> float | None:
    """≤ green → 0, ≥ red → 1, else linear (legacy save_labels_for_indicator)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= green:
        return 0.0
    if v >= red:
        return 1.0
    if red <= green:
        return 0.0
    return float((v - green) / (red - green))


def target_column_for_indicator(indicator: str) -> str:
    return f"{indicator.split('_')[0]}_t_target"


def percentile_range(values: list[float], lo: float = 85.0, hi: float = 95.0) -> dict[str, float]:
    """Auto range when indicator changes (legacy p85–p95)."""
    import numpy as np

    arr = np.asarray([float(v) for v in values if v is not None and np.isfinite(v)], dtype=np.float64)
    if arr.size == 0:
        return {"min": 0.0, "max": 1.0}
    if arr.size == 1:
        v = float(arr[0])
        return {"min": v - 0.5, "max": v + 0.5}
    p_lo = float(np.percentile(arr, lo))
    p_hi = float(np.percentile(arr, hi))
    if p_hi <= p_lo:
        p_hi = p_lo + 1e-3
    return {"min": p_lo, "max": p_hi}


def colorize_feature_props(
    props: dict[str, Any],
    *,
    indicator: str,
    thermal_coloring: bool,
    color_range: dict[str, float] | None,
    global_min: float,
    global_max: float,
) -> str:
    if not thermal_coloring:
        return iou_fallback_color(props.get("iou"))
    val = props.get(indicator)
    return get_thermal_color_for_value(val if val is not None else None, global_min, global_max, color_range)
