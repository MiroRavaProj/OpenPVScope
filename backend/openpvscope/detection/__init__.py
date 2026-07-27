"""Panel detection package."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name in (
        "detection_status",
        "generate_grid",
        "load_geojson",
        "save_aoi_geojson",
        "copy_rgb_grid_to_thermal",
        "clear_detection",
        "detection_dir",
    ):
        from openpvscope.detection import pipeline as p

        return getattr(p, name)
    if name in ("detection_job_status", "start_detection_job", "request_cancel_detection"):
        from openpvscope.detection import jobs as j

        return getattr(j, name)
    if name in ("load_detection_params", "save_detection_params", "default_detection_params"):
        from openpvscope.detection import prefs as pr

        return getattr(pr, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "detection_status",
    "generate_grid",
    "load_geojson",
    "save_aoi_geojson",
    "copy_rgb_grid_to_thermal",
    "clear_detection",
    "detection_dir",
    "detection_job_status",
    "start_detection_job",
    "request_cancel_detection",
    "load_detection_params",
    "save_detection_params",
    "default_detection_params",
]
