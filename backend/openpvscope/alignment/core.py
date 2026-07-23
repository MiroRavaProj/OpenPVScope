"""4-point affine alignment (from ortho_aligner) — metadata-only GeoTIFF rewrite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np


def estimate_affine(
    target_points: Sequence[Sequence[float]],
    ref_points: Sequence[Sequence[float]],
) -> np.ndarray:
    """
    Estimate 3x3 affine matrix mapping target pixel coords → reference pixel coords.
    Requires at least 4 point pairs.
    """
    src = np.asarray(target_points, dtype=np.float64)
    dst = np.asarray(ref_points, dtype=np.float64)
    if src.shape[0] < 4 or dst.shape[0] < 4:
        raise ValueError("At least 4 corresponding points are required")
    if src.shape != dst.shape:
        raise ValueError("Point arrays must have the same shape")

    from skimage.transform import AffineTransform

    tform = AffineTransform()
    if not tform.estimate(src, dst):
        raise RuntimeError("Failed to estimate affine transform from control points")
    if tform.params is None:
        raise RuntimeError("Affine transform has no parameters")
    return np.asarray(tform.params, dtype=np.float64)


def apply_georef_rewrite(
    reference_path: Path,
    target_path: Path,
    output_path: Path,
    target_points: Sequence[Sequence[float]],
    ref_points: Sequence[Sequence[float]],
) -> dict:
    """
    Write a copy of the target GeoTIFF with updated transform so that target
    pixels map into the reference CRS/world coordinates.

    Does NOT resample pixel values — only updates georeferencing metadata.
    """
    import rasterio
    from rasterio.transform import Affine

    affine_matrix = estimate_affine(target_points, ref_points)
    affine_px = Affine(
        affine_matrix[0, 0],
        affine_matrix[0, 1],
        affine_matrix[0, 2],
        affine_matrix[1, 0],
        affine_matrix[1, 1],
        affine_matrix[1, 2],
    )

    with rasterio.open(reference_path) as ref_ds, rasterio.open(target_path) as tgt_ds:
        new_transform = ref_ds.transform * affine_px
        profile = tgt_ds.profile.copy()
        profile["transform"] = new_transform
        # Prefer reference CRS if target CRS was wrong/missing
        if ref_ds.crs:
            profile["crs"] = ref_ds.crs
        data = tgt_ds.read()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(data)

    return {
        "output": str(output_path),
        "affine_matrix": affine_matrix.tolist(),
        "transform": list(new_transform)[:6],
    }


def save_alignment_artifacts(
    project_root: Path,
    ref_points: Sequence[Sequence[float]],
    target_points: Sequence[Sequence[float]],
    result: dict,
) -> None:
    align_dir = Path(project_root) / "alignment"
    align_dir.mkdir(parents=True, exist_ok=True)
    (align_dir / "gcps.json").write_text(
        json.dumps(
            {
                "ref_points": [list(p) for p in ref_points],
                "target_points": [list(p) for p in target_points],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (align_dir / "transform.json").write_text(
        json.dumps(
            {
                "affine_matrix": result["affine_matrix"],
                "transform": result["transform"],
                "output": result["output"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
