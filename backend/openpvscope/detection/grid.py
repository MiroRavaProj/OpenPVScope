"""AOI regularization and rows×cols grid in geographic / projected space."""

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]  # (x, y) = (lon/easting, lat/northing)


def _dist(a: Point, b: Point) -> float:
    return float(math.hypot(b[0] - a[0], b[1] - a[1]))


def regularize_quad(points: Sequence[Point]) -> list[Point]:
    """
    Turn a 4-point ring (click order around the boundary) into a parallelogram
    / rectangle that keeps the user's edge directions and side (no flip).

    Uses averaged opposite edges so the result stays on the same side of the
    first edge as the clicks — fixes AOIs jumping "above" the top corners.
    """
    pts = [(float(p[0]), float(p[1])) for p in points[:4]]
    if len(pts) != 4:
        raise ValueError("AOI must have exactly 4 corners")
    p0, p1, p2, p3 = pts
    if _dist(p0, p1) < 1e-12 or _dist(p1, p2) < 1e-12:
        raise ValueError("Degenerate AOI")

    # Average opposite edges — preserves winding / which side width goes
    ex = ((p1[0] - p0[0]) + (p2[0] - p3[0])) / 2.0
    ey = ((p1[1] - p0[1]) + (p2[1] - p3[1])) / 2.0
    fx = ((p3[0] - p0[0]) + (p2[0] - p1[0])) / 2.0
    fy = ((p3[1] - p0[1]) + (p2[1] - p1[1])) / 2.0

    if abs(ex) + abs(ey) < 1e-12 or abs(fx) + abs(fy) < 1e-12:
        raise ValueError("Degenerate AOI")

    # Optional: orthogonalize the short axis to the long axis while keeping sign(fx,fy)
    len_e = math.hypot(ex, ey)
    len_f = math.hypot(fx, fy)
    if len_e >= len_f:
        # Keep e as primary; make f perpendicular, same side as original f
        ux, uy = ex / len_e, ey / len_e
        # Perpendicular candidates
        px, py = -uy, ux
        if fx * px + fy * py < 0:
            px, py = uy, -ux
        fx, fy = px * len_f, py * len_f
        ex, ey = ux * len_e, uy * len_e
    else:
        ux, uy = fx / len_f, fy / len_f
        px, py = -uy, ux
        if ex * px + ey * py < 0:
            px, py = uy, -ux
        ex, ey = px * len_e, py * len_e
        fx, fy = ux * len_f, uy * len_f

    r0 = p0
    r1 = (p0[0] + ex, p0[1] + ey)
    r3 = (p0[0] + fx, p0[1] + fy)
    r2 = (p0[0] + ex + fx, p0[1] + ey + fy)
    return [r0, r1, r2, r3]


def _bilinear(p0: Point, p1: Point, p2: Point, p3: Point, u: float, v: float) -> Point:
    # p0=(0,0), p1=(1,0), p2=(1,1), p3=(0,1)
    x = (1 - v) * ((1 - u) * p0[0] + u * p1[0]) + v * ((1 - u) * p3[0] + u * p2[0])
    y = (1 - v) * ((1 - u) * p0[1] + u * p1[1]) + v * ((1 - u) * p3[1] + u * p2[1])
    return (float(x), float(y))


def build_grid_cells(
    rect: Sequence[Point],
    rows: int,
    cols: int,
) -> list[dict]:
    """
    Build rows×cols panel cells inside a regularized rectangle [p0,p1,p2,p3].
    Returns list of {row, col, ring: [[x,y],...]} (open rings, 4 corners).
    """
    if rows < 1 or cols < 1:
        raise ValueError("rows and cols must be >= 1")
    if len(rect) != 4:
        raise ValueError("rect must have 4 corners")
    p0, p1, p2, p3 = (tuple(map(float, p)) for p in rect)  # type: ignore[misc]
    cells: list[dict] = []
    for r in range(rows):
        v0 = r / rows
        v1 = (r + 1) / rows
        for c in range(cols):
            u0 = c / cols
            u1 = (c + 1) / cols
            ring = [
                _bilinear(p0, p1, p2, p3, u0, v0),
                _bilinear(p0, p1, p2, p3, u1, v0),
                _bilinear(p0, p1, p2, p3, u1, v1),
                _bilinear(p0, p1, p2, p3, u0, v1),
            ]
            cells.append({"row": r, "col": c, "ring": ring})
    return cells
