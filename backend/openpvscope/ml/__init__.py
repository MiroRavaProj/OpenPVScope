"""ML models / classification scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def ml_status(project_root: Path) -> dict[str, Any]:
    models_dir = Path(project_root) / "models"
    results = Path(project_root) / "classification" / "results.geojson"
    model_files = list(models_dir.glob("*.joblib")) if models_dir.is_dir() else []
    return {
        "ready": False,
        "message": "Models/classification scaffold — training UI comes after detection/segmentation.",
        "model_count": len(model_files),
        "has_results": results.is_file(),
    }
