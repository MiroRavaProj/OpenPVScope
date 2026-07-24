"""Save thermal soft labels from histogram range (legacy labeling)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from openpvscope.segmentation.thermal_color import (
    LABEL_INDICATORS,
    soft_label,
    target_column_for_indicator,
)


def save_thermal_labels(
    project_root: Path,
    *,
    indicator: str,
    green_threshold: float,
    red_threshold: float,
) -> dict[str, Any]:
    if indicator not in LABEL_INDICATORS:
        raise ValueError(f"Indicator not labelable: {indicator}")

    root = Path(project_root)
    pairs_path = root / "segmentation" / "pairs.json"
    if not pairs_path.is_file():
        raise FileNotFoundError("No pairs.json — run segmentation first")

    data = json.loads(pairs_path.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    target_col = target_column_for_indicator(indicator)

    out_path = root / "segmentation" / "thermal_panel_labels.csv"
    # Load existing rows keyed by pair id if present
    existing: dict[str, dict[str, Any]] = {}
    fieldnames = [
        "panel_pair_id",
        "max_temperature",
        "min_temperature",
        "mean_temperature",
        "median_temperature",
        "std_temperature",
        "var_temperature",
        "max_t_target",
        "mean_t_target",
        "median_t_target",
        "std_t_target",
    ]
    if out_path.is_file():
        with out_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                pid = row.get("panel_pair_id")
                if pid:
                    existing[pid] = row

    label_0 = label_1 = label_mid = 0
    for pair in pairs:
        pid = str(pair.get("id") or "")
        if not pid:
            continue
        stats = pair.get("stats") or {}
        row = existing.get(pid) or {k: "" for k in fieldnames}
        row["panel_pair_id"] = pid
        for key in (
            "max_temperature",
            "min_temperature",
            "mean_temperature",
            "median_temperature",
            "std_temperature",
            "var_temperature",
        ):
            val = stats.get(key)
            if val is None:
                val = pair.get(key)
            row[key] = "" if val is None else str(val)

        raw = stats.get(indicator)
        if raw is None:
            raw = pair.get(indicator)
        try:
            num = float(raw) if raw is not None and raw != "" else None
        except (TypeError, ValueError):
            num = None
        lab = soft_label(num, green_threshold, red_threshold)
        if lab is None:
            row[target_col] = ""
        else:
            row[target_col] = f"{lab:.6f}"
            if lab <= 0.0:
                label_0 += 1
            elif lab >= 1.0:
                label_1 += 1
            else:
                label_mid += 1
        existing[pid] = row

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for pid in sorted(existing.keys()):
            writer.writerow(existing[pid])

    return {
        "path": str(out_path),
        "indicator": indicator,
        "target_column": target_col,
        "labeled": label_0 + label_1 + label_mid,
        "label_0": label_0,
        "label_mid": label_mid,
        "label_1": label_1,
        "green": green_threshold,
        "red": red_threshold,
    }
