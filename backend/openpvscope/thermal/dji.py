"""Thermal image format detection and DJI R-JPEG → float32 TIFF conversion."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from openpvscope.thermal.dji_convert import process_rjpeg_to_float_tiff
from openpvscope.thermal.dji_sdk import probe_dji_sdk

__all__ = [
    "ThermalFormat",
    "detect_thermal_format",
    "convert_dji_thermal",
    "prepare_thermal_for_photogrammetry",
    "probe_dji_sdk",
]


class ThermalFormat(str, Enum):
    TIFF = "tiff"
    DJI_PROPRIETARY = "dji_proprietary"
    UNKNOWN = "unknown"


_DJI_EXTENSIONS = {".jpg", ".jpeg", ".rjpeg", ".raw", ".thm", ".rir"}
_TIFF_EXTENSIONS = {".tif", ".tiff"}


def detect_thermal_format(path: Path) -> ThermalFormat:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _TIFF_EXTENSIONS:
        return ThermalFormat.TIFF
    if suffix in _DJI_EXTENSIONS:
        if suffix in {".thm", ".rir"}:
            return ThermalFormat.DJI_PROPRIETARY
        try:
            head = path.read_bytes()[:64]
        except OSError:
            return ThermalFormat.UNKNOWN
        if head[:2] == b"\xff\xd8":
            return ThermalFormat.DJI_PROPRIETARY
        return ThermalFormat.UNKNOWN
    return ThermalFormat.UNKNOWN


def convert_dji_thermal(
    source: Path,
    dest_tiff: Path,
    *,
    emissivity: float = 0.95,
    distance: float = 5.0,
    humidity: float = 50.0,
    reflection: float = 25.0,
    parametric_fallback: bool = False,
) -> Path:
    """
    Convert DJI proprietary thermal (R-JPEG) to a float32 TIFF.

    Requires the DJI Thermal SDK under engines/dji_tsdk/ (or OPENPVSCOPE_DJI_TSDK).
    When ``parametric_fallback`` is True and SDK measurement fails, an optional
    bundled MLP may estimate temperature from raw counts.
    """
    return process_rjpeg_to_float_tiff(
        Path(source),
        Path(dest_tiff),
        emissivity=emissivity,
        distance=distance,
        humidity=humidity,
        reflection=reflection,
        parametric_fallback=parametric_fallback,
    )


def prepare_thermal_for_photogrammetry(source: Path, dest_dir: Path, **params) -> Path | None:
    """
    Ensure a TIFF suitable for photogrammetry (ODX) exists in dest_dir.

    TIFF inputs are copied as-is; DJI inputs go through convert_dji_thermal.
    Visible-light companions (``_V.`` in the filename) are skipped (returns None).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    source = Path(source)

    if "_V." in source.name:
        return None

    fmt = detect_thermal_format(source)

    if fmt == ThermalFormat.TIFF:
        dest = dest_dir / source.name
        if source.resolve() != dest.resolve():
            dest.write_bytes(source.read_bytes())
        return dest

    if fmt == ThermalFormat.DJI_PROPRIETARY:
        dest = dest_dir / f"{source.stem}.tif"
        return convert_dji_thermal(source, dest, **params)

    raise ValueError(f"Unsupported thermal format for {source} ({fmt})")
