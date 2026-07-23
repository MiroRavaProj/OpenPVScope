"""Export helpers scaffold."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def exports_status(project_root: Path) -> dict[str, Any]:
    exp = Path(project_root) / "exports"
    files = [p.name for p in exp.iterdir()] if exp.is_dir() else []
    return {"files": files}


def write_anomalies_csv(project_root: Path, rows: list[dict[str, Any]]) -> Path:
    out = Path(project_root) / "exports" / "anomalies.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out.write_text("id,label,note\n", encoding="utf-8")
        return out
    fieldnames = list(rows[0].keys())
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return out
