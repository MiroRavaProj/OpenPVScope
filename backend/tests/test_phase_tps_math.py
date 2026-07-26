"""Smoke tests for AKAZE + TPS alignment (live copy under openpvscope.alignment)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from openpvscope.alignment.register_thermal import (
    MAX_REG_GSD_M,
    _create_akaze,
    register_thermal_to_rgb,
    upsample_flow,
)


def test_max_reg_gsd_default() -> None:
    assert MAX_REG_GSD_M == 0.02


def test_akaze_detector_constructs() -> None:
    det = _create_akaze()
    assert det is not None


def test_upsample_flow_scales_displacements() -> None:
    flow = np.ones((10, 20, 2), dtype=np.float32)
    flow[..., 0] = 2.0
    flow[..., 1] = 4.0
    up = upsample_flow(flow, (20, 40))
    assert up.shape == (20, 40, 2)
    np.testing.assert_allclose(up[..., 0], 4.0, atol=1e-5)
    np.testing.assert_allclose(up[..., 1], 8.0, atol=1e-5)


def test_register_akaze_smoke(tmp_path: Path) -> None:
    h, w = 256, 256
    px_deg = 0.03 / 111320.0
    transform = from_origin(10.0, 45.0, px_deg, px_deg)
    profile = dict(
        driver="GTiff",
        height=h,
        width=w,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=-32767,
    )
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = ((np.sin(xx / 12.0) + np.cos(yy / 9.0)) * 40 + 120).astype(np.float32)
    # Mild scale + shift so Pass-1 affine has something to recover
    M = np.float32([[1.02, 0.0, -4.0], [0.0, 1.02, 3.0]])
    th = __import__("cv2").warpAffine(
        rgb, M, (w, h), flags=__import__("cv2").INTER_LINEAR | __import__("cv2").WARP_INVERSE_MAP
    )
    rgb_p = tmp_path / "rgb.tif"
    th_p = tmp_path / "th.tif"
    out = tmp_path / "out.tif"
    for p, a in ((rgb_p, rgb), (th_p, th.astype(np.float32))):
        with rasterio.open(p, "w", **profile) as ds:
            ds.write(a, 1)

    meta = register_thermal_to_rgb(rgb_p, th_p, out, max_reg_gsd_m=0.02)
    assert out.is_file()
    assert (tmp_path / "out_pass1.tif").is_file()
    assert meta["method"] == "akaze_tps"
    assert meta["output_grid"] == "native_thermal"
    assert meta["native_shape_hw"] == [h, w]
    assert meta["pass1"] in {
        "tile+akaze+ecc_affine",
        "tile+akaze_affine",
        "phase_fallback",
    }
