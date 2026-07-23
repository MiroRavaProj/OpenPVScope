"""CRS / GeoJSON helpers for map overlays (MapLibre expects WGS84)."""

from __future__ import annotations

from typing import Any, Sequence

from pyproj import Transformer


def transformer_to_wgs84(crs: Any) -> Transformer | None:
    if crs is None:
        return None
    try:
        import rasterio

        if isinstance(crs, str):
            src = crs
        else:
            src = crs.to_string() if hasattr(crs, "to_string") else str(crs)
        if "4326" in src.replace(" ", ""):
            return None
        return Transformer.from_crs(src, "EPSG:4326", always_xy=True)
    except Exception:
        return None


def xy_to_lonlat(x: float, y: float, transformer: Transformer | None) -> tuple[float, float]:
    if transformer is None:
        return float(x), float(y)
    lon, lat = transformer.transform(x, y)
    return float(lon), float(lat)


def ring_to_lonlat(
    ring: Sequence[Sequence[float]],
    transformer: Transformer | None,
) -> list[list[float]]:
    """ring as [[x,y], ...] → [[lon,lat], ...] (closed if input closed)."""
    out: list[list[float]] = []
    for pt in ring:
        lon, lat = xy_to_lonlat(float(pt[0]), float(pt[1]), transformer)
        out.append([lon, lat])
    return out


def bounds_to_wgs84(
    left: float,
    bottom: float,
    right: float,
    top: float,
    crs: Any,
) -> dict[str, float]:
    tr = transformer_to_wgs84(crs)
    corners = [
        xy_to_lonlat(left, bottom, tr),
        xy_to_lonlat(right, bottom, tr),
        xy_to_lonlat(right, top, tr),
        xy_to_lonlat(left, top, tr),
    ]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return {
        "left": min(lons),
        "right": max(lons),
        "bottom": min(lats),
        "top": max(lats),
    }


def feature_collection(features: list[dict], *, name: str | None = None) -> dict:
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if name:
        fc["name"] = name
    return fc


def polygon_feature(
    ring_lonlat: Sequence[Sequence[float]],
    properties: dict | None = None,
    *,
    fid: str | None = None,
) -> dict:
    coords = [list(pt) for pt in ring_lonlat]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    feat: dict[str, Any] = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords]},
        "properties": properties or {},
    }
    if fid is not None:
        feat["id"] = fid
        feat["properties"]["id"] = fid
    return feat
