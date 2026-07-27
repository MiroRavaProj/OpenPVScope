"""Lightweight RGB↔thermal pair preview (no crop extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openpvscope.detection import load_geojson
from openpvscope.geo.crs import feature_collection, polygon_feature
from openpvscope.segmentation.pairing import pair_rgb_thermal_panels

ProgressCb = Callable[..., None]
LogCb = Callable[[str, str], None]


def build_pair_preview_geojson(
    project_root: Path,
    *,
    min_iou: float = 0.0,
    search_radius_m: float | None = None,
    progress: ProgressCb | None = None,
    log: LogCb | None = None,
) -> dict[str, Any]:
    """
    Build candidate RGB↔thermal pairs.

    Default min_iou=0 returns the full greedy assignment so the client can
    filter by IoU instantly without re-pairing.
    """
    root = Path(project_root)
    if progress:
        try:
            progress(2, "Loading RGB / thermal panels for IoU preview…", level="info")
        except TypeError:
            progress(2, "Loading RGB / thermal panels for IoU preview…")
    rgb = load_geojson(root, "panels", modality="rgb")
    thermal = load_geojson(root, "panels", modality="thermal")
    if not rgb or not rgb.get("features"):
        raise FileNotFoundError("No RGB panels — run RGB detection first")
    if not thermal or not thermal.get("features"):
        raise FileNotFoundError("No thermal panels — run thermal detection first")
    if log:
        log(
            "verbose",
            f"Loaded {len(rgb.get('features') or [])} RGB + "
            f"{len(thermal.get('features') or [])} thermal features",
        )

    pairs = pair_rgb_thermal_panels(
        rgb,
        thermal,
        search_radius_m=search_radius_m,
        min_iou=float(min_iou),
        progress=progress,
        log=log,
    )
    features = []
    for p in pairs:
        ring = p.get("ring") or p.get("rgb_ring") or []
        if len(ring) < 4:
            continue
        features.append(
            polygon_feature(
                [[float(x), float(y)] for x, y in ring[:4]],
                {
                    "kind": "pair_preview",
                    "id": p["id"],
                    "rgb_id": p.get("rgb_id"),
                    "thermal_id": p.get("thermal_id"),
                    "iou": p.get("iou"),
                    "distance_m": p.get("distance_m"),
                    "confidence": p.get("confidence"),
                    "thermal_confidence": p.get("thermal_confidence"),
                },
                fid=str(p["id"]),
            )
        )
    if progress:
        try:
            progress(100, f"Preview ready: {len(features)} candidate pairs", level="info")
        except TypeError:
            progress(100, f"Preview ready: {len(features)} candidate pairs")
    return {
        **feature_collection(features, name="pair_preview"),
        "count": len(features),
        "min_iou": float(min_iou),
    }
