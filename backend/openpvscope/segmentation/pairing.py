"""Pair RGB panel polygons with thermal coverage (aligned CRS)."""

from __future__ import annotations

from typing import Any


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    xs = [p[0] for p in ring[:-1] if len(p) >= 2] or [p[0] for p in ring]
    ys = [p[1] for p in ring[:-1] if len(p) >= 2] or [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def pair_panels_self(
    panels_fc: dict[str, Any],
    *,
    max_center_dist_m: float = 8.0,
) -> list[dict[str, Any]]:
    """
    v1 pairing: each RGB panel is its own pair (thermal crop uses same geo ring
    on thermal_aligned). Distance/IoU fields kept for API compatibility.
    """
    _ = max_center_dist_m
    pairs: list[dict[str, Any]] = []
    for feat in panels_fc.get("features") or []:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        ring = geom["coordinates"][0]
        pid = str((feat.get("properties") or {}).get("id") or feat.get("id") or "")
        if not pid:
            continue
        cx, cy = _ring_centroid(ring)
        pairs.append(
            {
                "id": pid,
                "rgb_id": pid,
                "thermal_id": pid,
                "center": [cx, cy],
                "ring": ring,
                "iou": 1.0,
                "distance_m": 0.0,
                "confidence": float((feat.get("properties") or {}).get("confidence") or 0),
            }
        )
    pairs.sort(key=lambda p: p["id"])
    return pairs
