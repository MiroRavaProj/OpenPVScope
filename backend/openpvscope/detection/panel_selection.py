"""Persist panel audit GeoJSON and derive segmentation input from includes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from openpvscope.detection.refine import DEFAULT_INCLUDE_FATES
from openpvscope.geo.crs import feature_collection
from openpvscope.io_atomic import atomic_write_json

Modality = Literal["rgb", "thermal"]


def detection_dir(root: Path, modality: Modality = "rgb") -> Path:
    return Path(root) / "detection" / modality

FATE_DEFAULT_INCLUDE = {f: (f in DEFAULT_INCLUDE_FATES) for f in (
    "kept",
    "filled_restored",
    "filled_synth",
    "readded",
    "dbscan_noise",
    "tiny_cluster",
    "walk_reject",
    "no_fit",
    "border_prune",
)}


def panels_all_path(root: Path, modality: Modality) -> Path:
    return detection_dir(root, modality) / "panels_all.geojson"


def panels_path(root: Path, modality: Modality) -> Path:
    return detection_dir(root, modality) / "panels.geojson"


def write_panels_from_features(
    root: Path,
    modality: Modality,
    features_all: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write panels_all.geojson and panels.geojson (include=true only)."""
    det_dir = detection_dir(root, modality)
    det_dir.mkdir(parents=True, exist_ok=True)
    fc_all = feature_collection(features_all, name="panels_all")
    # Compact JSON — panels_all can be huge; pretty-print made exclude/include slow.
    atomic_write_json(panels_all_path(root, modality), fc_all, indent=None)
    included = [
        f
        for f in features_all
        if bool((f.get("properties") or {}).get("include", False))
    ]
    atomic_write_json(
        panels_path(root, modality),
        feature_collection(included, name="panels"),
        indent=None,
    )
    return fate_counts(features_all)


def fate_counts(features: list[dict[str, Any]]) -> dict[str, Any]:
    by_fate: dict[str, dict[str, int]] = {}
    included = 0
    for f in features:
        props = f.get("properties") or {}
        fate = str(props.get("fate") or "kept")
        bucket = by_fate.setdefault(fate, {"total": 0, "included": 0})
        bucket["total"] += 1
        if props.get("include"):
            bucket["included"] += 1
            included += 1
    return {
        "total": len(features),
        "included": included,
        "by_fate": by_fate,
    }


def load_panels_all(root: Path, modality: Modality) -> dict[str, Any] | None:
    path = panels_all_path(root, modality)
    if not path.is_file():
        # Fallback: promote panels.geojson to audit-shaped features
        p = panels_path(root, modality)
        if not p.is_file():
            return None
        try:
            fc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        for f in fc.get("features") or []:
            props = f.setdefault("properties", {})
            props.setdefault("fate", "kept")
            props.setdefault("include", True)
        return fc
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def apply_panel_selection(
    root: Path,
    modality: Modality,
    *,
    include_ids: list[str] | None = None,
    exclude_ids: list[str] | None = None,
    set_fate: dict[str, Any] | None = None,
    reset_defaults: bool = False,
) -> dict[str, Any]:
    """
    Update include flags on panels_all and rewrite panels.geojson.

    set_fate: { "fate": "walk_reject", "include": true }
    """
    fc = load_panels_all(root, modality)
    if not fc:
        raise FileNotFoundError("No panels_all / panels for modality")

    feats = list(fc.get("features") or [])
    include_set = {str(x) for x in (include_ids or [])}
    exclude_set = {str(x) for x in (exclude_ids or [])}
    fate_patch = set_fate or {}
    fate_name = fate_patch.get("fate")
    fate_include = fate_patch.get("include")

    for f in feats:
        props = f.setdefault("properties", {})
        pid = str(props.get("id") or f.get("id") or "")
        fate = str(props.get("fate") or "kept")
        if reset_defaults:
            props["include"] = bool(FATE_DEFAULT_INCLUDE.get(fate, fate in DEFAULT_INCLUDE_FATES))
            continue
        if fate_name is not None and fate == str(fate_name) and fate_include is not None:
            props["include"] = bool(fate_include)
        if pid and pid in include_set:
            props["include"] = True
        if pid and pid in exclude_set:
            props["include"] = False

    counts = write_panels_from_features(root, modality, feats)
    return {"ok": True, "modality": modality, **counts}


def set_panel_include(
    root: Path,
    modality: Modality,
    panel_id: str,
    include: bool,
) -> dict[str, Any]:
    if include:
        return apply_panel_selection(root, modality, include_ids=[panel_id])
    return apply_panel_selection(root, modality, exclude_ids=[panel_id])
