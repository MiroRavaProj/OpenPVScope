"""Deskew AOI — longest side measured in pixel space (legacy suite)."""

from __future__ import annotations

import math
from typing import Any, Sequence

import cv2
import numpy as np


def aoi_deskew_angle_deg(
    ring_lonlat: Sequence[Sequence[float]],
    *,
    affine: Any | None = None,
) -> float:
    """
    OpenCV rotation degrees (CCW+) aligning AOI longest side to nearest cardinal.

    With ``affine`` (rasterio Affine), side lengths use pixel distance via rowcol
    (xs=lon, ys=lat) — same intent as the legacy suite.
    ring: [[lon, lat], ...] ×4
    """
    pts = [(float(p[0]), float(p[1])) for p in ring_lonlat[:4]]
    if len(pts) != 4:
        raise ValueError("AOI needs 4 corners")

    sides = []
    for i in range(4):
        p1 = pts[i]
        p2 = pts[(i + 1) % 4]
        if affine is not None:
            import rasterio.transform

            r1, c1 = rasterio.transform.rowcol(affine, p1[0], p1[1])
            r2, c2 = rasterio.transform.rowcol(affine, p2[0], p2[1])
            length = math.hypot(float(c2 - c1), float(r2 - r1))
        else:
            length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        sides.append({"p1": p1, "p2": p2, "length": length})

    longest = max(sides, key=lambda s: s["length"])
    p1, p2 = longest["p1"], longest["p2"]
    if p1[0] <= p2[0]:
        west, east = p1, p2
    else:
        west, east = p2, p1

    delta_lng = east[0] - west[0]
    delta_lat = east[1] - west[1]
    bearing_deg = math.degrees(math.atan2(delta_lng, delta_lat))
    if bearing_deg < 0:
        bearing_deg += 360.0

    if bearing_deg <= 45 or bearing_deg > 315:
        if bearing_deg > 315:
            rotation_needed = (bearing_deg - 360.0) - 0.0
        else:
            rotation_needed = bearing_deg - 0.0
    elif bearing_deg <= 135:
        rotation_needed = bearing_deg - 90.0
    elif bearing_deg <= 225:
        rotation_needed = bearing_deg - 180.0
    else:
        rotation_needed = bearing_deg - 270.0

    return float(rotation_needed)


def rotation_matrix_expanded(
    width: int,
    height: int,
    angle_deg: float,
) -> tuple[np.ndarray, int, int]:
    """2x3 affine M and (new_w, new_h) — expand-canvas warp."""
    center = (width // 2, height // 2)
    m = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos_a = abs(m[0, 0])
    sin_a = abs(m[0, 1])
    new_w = int((height * sin_a) + (width * cos_a))
    new_h = int((height * cos_a) + (width * sin_a))
    m[0, 2] += (new_w / 2.0) - center[0]
    m[1, 2] += (new_h / 2.0) - center[1]
    return m, new_w, new_h


def warp_image(image: np.ndarray, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Warp HxW or HxWxC; returns (rotated, M 2x3)."""
    h, w = image.shape[:2]
    m, nw, nh = rotation_matrix_expanded(w, h, angle_deg)
    border = 0 if image.ndim == 2 else (0, 0, 0)
    rotated = cv2.warpAffine(
        image,
        m,
        (nw, nh),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border,
    )
    return rotated, m


def warp_rgb(image_rgb: np.ndarray, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    return warp_image(image_rgb, angle_deg)


def apply_m(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    nx = m[0, 0] * x + m[0, 1] * y + m[0, 2]
    ny = m[1, 0] * x + m[1, 1] * y + m[1, 2]
    return float(nx), float(ny)


def invert_m(m: np.ndarray) -> np.ndarray:
    return cv2.invertAffineTransform(m)


def oriented_quads_from_seed(
    seed_ring_lonlat: list[list[float]],
    centers_lonlat: list[tuple[float, float]],
) -> list[list[list[float]]]:
    ring = [[float(p[0]), float(p[1])] for p in seed_ring_lonlat[:4]]
    if len(ring) != 4:
        raise ValueError("seed ring needs 4 corners")
    sx = sum(p[0] for p in ring) / 4.0
    sy = sum(p[1] for p in ring) / 4.0
    out: list[list[list[float]]] = []
    for cx, cy in centers_lonlat:
        dx, dy = cx - sx, cy - sy
        out.append([[p[0] + dx, p[1] + dy] for p in ring])
    return out
