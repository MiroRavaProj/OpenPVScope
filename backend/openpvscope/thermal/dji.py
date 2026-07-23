"""Thermal image format detection and DJI conversion hook."""

from __future__ import annotations

from enum import Enum
from pathlib import Path


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


def convert_dji_thermal(source: Path, dest_tiff: Path) -> Path:
    """
    Convert DJI proprietary thermal to TIFF.

    Stub: plug in the author's converter later.
    """
    raise NotImplementedError(
        "DJI thermal conversion is not bundled yet. "
        "Provide convert_dji_thermal implementation when ready. "
        f"Source was: {source}"
    )


def prepare_thermal_for_opensfm(source: Path, dest_dir: Path) -> Path:
    """
    Ensure a TIFF suitable for OpenSfM exists in dest_dir.
    TIFF inputs are copied as-is; DJI inputs go through convert_dji_thermal.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    fmt = detect_thermal_format(source)

    if fmt == ThermalFormat.TIFF:
        dest = dest_dir / source.name
        if source.resolve() != dest.resolve():
            dest.write_bytes(source.read_bytes())
        return dest

    if fmt == ThermalFormat.DJI_PROPRIETARY:
        dest = dest_dir / f"{source.stem}.tif"
        return convert_dji_thermal(source, dest)

    raise ValueError(f"Unsupported thermal format for {source} ({fmt})")
