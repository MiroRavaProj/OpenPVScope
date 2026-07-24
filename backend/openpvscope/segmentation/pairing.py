"""
RGB ↔ thermal panel pairing (legacy suite geographic match).

Port of utils/segmentation/panel_pairing.py:
  - search by center distance (meters)
  - score by approximate IoU of equal-size boxes around centers
  - greedy 1:1 assignment (each thermal panel used at most once)
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import numpy as np

DEFAULT_SEARCH_RADIUS_M = 4.0
DEFAULT_MIN_IOU = 0.1
# ~2 m at equator — same default as legacy calculate_iou_from_centers
DEFAULT_PANEL_SIZE_DEG = 0.000018


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """Return (lon, lat) centroid of a GeoJSON ring."""
    pts = [p for p in ring if len(p) >= 2]
    if len(pts) > 1 and pts[0][0] == pts[-1][0] and pts[0][1] == pts[-1][1]:
        pts = pts[:-1]
    if not pts:
        return 0.0, 0.0
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def distance_meters(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Equirectangular distance in meters (legacy calculate_distance_meters)."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    lng1_r = math.radians(lon1)
    lng2_r = math.radians(lon2)
    r = 6_371_000.0
    x = (lng2_r - lng1_r) * math.cos((lat1_r + lat2_r) / 2.0)
    y = lat2_r - lat1_r
    return r * math.sqrt(x * x + y * y)


def iou_from_centers(
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
    *,
    panel_size_deg: float = DEFAULT_PANEL_SIZE_DEG,
) -> float:
    """
    Approximate IoU of equal-size lon/lat boxes around centers.
    Legacy used (lat, lng) order; we keep lon/lat externally and map internally.
    """
    half = panel_size_deg / 2.0
    # boxes as [min_lat, min_lng, max_lat, max_lng] like legacy
    box1 = [lat1 - half, lon1 - half, lat1 + half, lon1 + half]
    box2 = [lat2 - half, lon2 - half, lat2 + half, lon2 + half]
    inter_min_lat = max(box1[0], box2[0])
    inter_min_lng = max(box1[1], box2[1])
    inter_max_lat = min(box1[2], box2[2])
    inter_max_lng = min(box1[3], box2[3])
    if inter_min_lat >= inter_max_lat or inter_min_lng >= inter_max_lng:
        return 0.0
    inter = (inter_max_lat - inter_min_lat) * (inter_max_lng - inter_min_lng)
    a1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    a2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = a1 + a2 - inter
    return float(inter / union) if union > 0 else 0.0


def _features_as_panels(fc: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not fc:
        return out
    for feat in fc.get("features") or []:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        ring = geom.get("coordinates") or []
        if not ring:
            continue
        coords = ring[0]
        props = feat.get("properties") or {}
        pid = str(props.get("id") or feat.get("id") or "")
        if not pid:
            continue
        lon, lat = _ring_centroid(coords)
        out.append(
            {
                "id": pid,
                "ring": coords,
                "center_lon": lon,
                "center_lat": lat,
                "confidence": float(props.get("confidence") or 0.0),
                "modality": props.get("modality"),
            }
        )
    return out


def estimate_panel_size_deg(panels: list[dict[str, Any]]) -> float:
    """Median side length in degrees from panel rings (fallback to legacy default)."""
    sides: list[float] = []
    for p in panels[:200]:
        ring = p["ring"]
        pts = [q for q in ring if len(q) >= 2]
        if len(pts) > 1 and pts[0][0] == pts[-1][0] and pts[0][1] == pts[-1][1]:
            pts = pts[:-1]
        if len(pts) < 2:
            continue
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            sides.append(math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])))
    if not sides:
        return DEFAULT_PANEL_SIZE_DEG
    return float(max(np.median(sides), DEFAULT_PANEL_SIZE_DEG * 0.25))


def estimate_search_radius_m(panels: list[dict[str, Any]]) -> float:
    """~2× max panel side in meters (legacy UI: 2 × max(panel_w, panel_h))."""
    if not panels:
        return DEFAULT_SEARCH_RADIUS_M
    p0 = panels[0]
    lon, lat = p0["center_lon"], p0["center_lat"]
    size_deg = estimate_panel_size_deg(panels)
    # convert size_deg at this latitude to meters (approx)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
    side_m = size_deg * max(m_per_deg_lat, abs(m_per_deg_lon))
    return float(max(DEFAULT_SEARCH_RADIUS_M, 2.0 * side_m))


def pair_rgb_thermal_panels(
    rgb_fc: dict[str, Any],
    thermal_fc: dict[str, Any],
    *,
    search_radius_m: float | None = None,
    min_iou: float = DEFAULT_MIN_IOU,
    panel_size_deg: float | None = None,
) -> list[dict[str, Any]]:
    """
    Pair RGB panels to thermal panels (legacy greedy match).

    Returns pairs with rgb_ring + thermal_ring for modality-specific crops.
    """
    rgb_panels = _features_as_panels(rgb_fc)
    th_panels = _features_as_panels(thermal_fc)
    if not rgb_panels:
        raise FileNotFoundError("No RGB panels — run RGB detection first")
    if not th_panels:
        raise FileNotFoundError("No thermal panels — run thermal detection first")

    size_deg = panel_size_deg if panel_size_deg is not None else estimate_panel_size_deg(rgb_panels)
    radius = search_radius_m if search_radius_m is not None else estimate_search_radius_m(rgb_panels)

    used_thermal: set[str] = set()
    pairs: list[dict[str, Any]] = []

    for rgb in rgb_panels:
        best = None
        best_iou = 0.0
        best_dist = float("inf")
        for th in th_panels:
            if th["id"] in used_thermal:
                continue
            dist = distance_meters(
                rgb["center_lon"], rgb["center_lat"], th["center_lon"], th["center_lat"]
            )
            if dist > radius:
                continue
            iou = iou_from_centers(
                rgb["center_lon"],
                rgb["center_lat"],
                th["center_lon"],
                th["center_lat"],
                panel_size_deg=size_deg,
            )
            if iou > best_iou:
                best_iou = iou
                best = th
                best_dist = dist
        if best is None or best_iou < min_iou:
            continue
        used_thermal.add(best["id"])
        pair_id = uuid.uuid4().hex[:12]
        pairs.append(
            {
                "id": pair_id,
                "rgb_id": rgb["id"],
                "thermal_id": best["id"],
                "center": [rgb["center_lon"], rgb["center_lat"]],
                "rgb_ring": rgb["ring"],
                "thermal_ring": best["ring"],
                "ring": rgb["ring"],  # map display uses RGB ring
                "iou": float(best_iou),
                "distance_m": float(best_dist),
                "confidence": float(rgb["confidence"]),
                "thermal_confidence": float(best["confidence"]),
            }
        )

    pairs.sort(key=lambda p: p["id"])
    return pairs


# Back-compat alias used by older callers
def pair_panels_self(panels_fc: dict[str, Any], *, max_center_dist_m: float = 8.0) -> list[dict[str, Any]]:
    """Deprecated self-pair — keep for imports; prefer pair_rgb_thermal_panels."""
    _ = max_center_dist_m
    panels = _features_as_panels(panels_fc)
    out = []
    for p in panels:
        out.append(
            {
                "id": p["id"],
                "rgb_id": p["id"],
                "thermal_id": p["id"],
                "center": [p["center_lon"], p["center_lat"]],
                "rgb_ring": p["ring"],
                "thermal_ring": p["ring"],
                "ring": p["ring"],
                "iou": 1.0,
                "distance_m": 0.0,
                "confidence": p["confidence"],
            }
        )
    return out
