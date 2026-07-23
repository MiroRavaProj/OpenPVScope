"""Panel detection package."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name in (
        "detection_status",
        "generate_grid",
        "load_geojson",
        "save_aoi_geojson",
    ):
        from openpvscope.detection import pipeline as p

        return getattr(p, name)
    if name in ("detection_job_status", "start_detection_job"):
        from openpvscope.detection import jobs as j

        return getattr(j, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "detection_status",
    "generate_grid",
    "load_geojson",
    "save_aoi_geojson",
    "detection_job_status",
    "start_detection_job",
]
