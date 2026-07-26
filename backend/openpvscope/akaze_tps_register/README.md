# Thermal → RGB registration

Self-contained script: `register_thermal.py`. Copy this folder into a pipeline; it does not import the parent PoC.

**Contract**

- RGB is the geometric reference and is never warped.
- Registration runs on a **working grid** from the thermal, capped at `MAX_REG_GSD_M = 0.02` m/px (never finer than 2 cm).
- Main output = warped thermal at **native thermal** resolution / CRS / transform.
- Also writes `{output_stem}_pass1.tif` = Pass 1 only (global affine, no TPS).

---

## Algorithm

```
RGB, T  ──reproject──► working grid (gsd_work = max(gsd_thermal, 0.02 m))
         │
         ▼
   Sobel edge maps (uint8)
         │
         ▼
Pass 1 (on ≤2048px proxy, then scale matrix to full work res)
   tile phase-correlation pairs (generous shift ~15% of frame)
   + AKAZE matches (ORB fallback if AKAZE unavailable)
   → RANSAC estimateAffine2D
   → findTransformECC MOTION_AFFINE refine
   → warpAffine(..., WARP_INVERSE_MAP)
   If too few points: phaseCorrelate translation only.
         │
         ▼
   warp working thermal; recompute thermal edges
         │
         ▼
Pass 2
   tile phase (TILE_M / STRIDE_M) → TPS dense flow
   local clamp LOCAL_MAX_M
         │
         ▼
   apply Pass-1 affine + upsampled TPS on native thermal
   write output.tif and output_pass1.tif
```

### Constants (in `register_thermal.py`)

| Name | Value | Role |
|------|-------|------|
| `MAX_REG_GSD_M` | 0.02 m | Finest working GSD |
| `GLOBAL_MAX_M` | 2.5 m | Floor for Pass-1 tile shift budget; also clamps phase-fallback shift |
| `LOCAL_MAX_M` | 0.5 m | Pass-2 TPS residual clamp |
| `TILE_M` | 5.76 m | Pass-1/2 tile size |
| `STRIDE_M` | 3.84 m | Pass-1/2 tile stride |
| `NODATA` | -32767 | Output nodata |

Pass 1 does not clamp scale/stretch; RANSAC + ECC set the affine.

---

## API

```python
from register_thermal import register_thermal_to_rgb

meta = register_thermal_to_rgb(
    rgb_path="RGB.tif",
    thermal_path="T.tif",
    output_path="thermal_aligned.tif",
    nodata=-32767.0,     # optional
    max_reg_gsd_m=0.02,  # optional
)
```

### Metadata keys written by the script

| Key | Meaning |
|-----|---------|
| `output_path` / `pass1_output_path` | Full + Pass-1 GeoTIFF paths |
| `output_grid` | `"native_thermal"` |
| `gsd_work_m` / `gsd_thermal_m` | Working vs native thermal GSD |
| `work_shape_hw` / `native_shape_hw` | Array sizes |
| `pass1` | `"tile+akaze+ecc_affine"`, `"tile+akaze_affine"`, or `"phase_fallback"` |
| `n_tile_pairs` / `n_akaze_matches` / `n_matches` / `n_inliers` | Pass-1 correspondence stats |
| `scale_x` / `scale_y` / `rotation_deg` / `tx` / `ty` | Affine decomposition |
| `ecc_cc` | ECC correlation (or `None`) |
| `affine_2x3_work` | 2×3 matrix on the working grid |
| `ctrl_pts` | Pass-2 accepted tile count |
| `tile_px` / `stride_px` | Pass-2 window in working pixels |
| `prep_reproject_s` / `global_pass1_s` / `tile_tps_s` / `runtime_s` | Timings |

---

## CLI

```bash
pip install -r requirements.txt
python register_thermal.py RGB.tif T.tif -o thermal_aligned.tif
python register_thermal.py RGB.tif T.tif -o out.tif --max-reg-gsd-m 0.02
```

---

## Dependencies

Listed in `requirements.txt`: `numpy`, `scipy`, `opencv-python`, `rasterio`.

AKAZE is used when present (`cv2.AKAZE_create` or `cv2.xfeatures2d_AKAZE`); otherwise the script falls back to ORB. For AKAZE on some OpenCV builds, install `opencv-contrib-python` instead of `opencv-python`.
