"""Segmentation / pairing scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def segmentation_status(project_root: Path) -> dict[str, Any]:
    pairs = Path(project_root) / "segmentation" / "pairs.jsonl"
    return {
        "ready": False,
        "message": "Segmentation scaffold — RGB↔thermal pairing will be ported next.",
        "has_pairs": pairs.is_file(),
        "pair_count": sum(1 for _ in pairs.open(encoding="utf-8")) if pairs.is_file() else 0,
    }
