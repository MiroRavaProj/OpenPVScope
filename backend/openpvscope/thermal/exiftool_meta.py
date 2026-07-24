"""ExifTool metadata copy helpers for DJI thermal conversion."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from openpvscope.thermal.dji_sdk import find_dji_tsdk_root

_exiftool_path: Path | None = None
_exiftool_temp_dir: Path | None = None
_exiftool_setup_done = False


def _bundled_exiftool() -> Path | None:
    root = find_dji_tsdk_root()
    if root is None:
        return None
    # SDK root may be .../dji_tsdk or .../dji_tsdk/DJI_TSDK
    for base in (root, root.parent if root.name.upper() == "DJI_TSDK" else root):
        exe = base / "exiftool.exe"
        if exe.is_file():
            return exe
    return None


def setup_exiftool() -> Path | None:
    """
    Resolve exiftool.exe.

    On frozen builds, copies into a shared temp dir (with exiftool_files/) so
    relative Perl libs resolve. Dev checkouts use the bundled path directly.
    """
    global _exiftool_path, _exiftool_temp_dir, _exiftool_setup_done

    if _exiftool_setup_done:
        return _exiftool_path
    _exiftool_setup_done = True

    bundled = _bundled_exiftool()
    if bundled is None:
        _exiftool_path = None
        return None

    # Prefer in-place when not frozen (avoids copying ~30MB tree).
    if not getattr(sys, "frozen", False):
        _exiftool_path = bundled
        return _exiftool_path

    try:
        _exiftool_temp_dir = Path(tempfile.mkdtemp(prefix="openpvscope_exiftool_"))
        dest_exe = _exiftool_temp_dir / "exiftool.exe"
        shutil.copy2(bundled, dest_exe)
        files_src = bundled.parent / "exiftool_files"
        if files_src.is_dir():
            shutil.copytree(files_src, _exiftool_temp_dir / "exiftool_files")
        _exiftool_path = dest_exe
        return _exiftool_path
    except OSError:
        _exiftool_path = None
        return None


def copy_metadata_exiftool(
    src_jpg: Path | str,
    dst_path: Path | str,
    *,
    samples_per_pixel: str = "1",
) -> tuple[bool, str]:
    """Copy IPTC/EXIF/XMP from source R-JPEG onto dest TIFF via ExifTool."""
    exe = setup_exiftool()
    if exe is None:
        return False, "exiftool unavailable"

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        result = subprocess.run(
            [
                str(exe),
                "-TagsFromFile",
                str(src_jpg),
                f"-SamplesPerPixel={samples_per_pixel}",
                "-IPTC:all",
                "-exif:all",
                "-xmp:all",
                "-jfif:all",
                "-all:all>all:all",
                str(dst_path),
                "-overwrite_original",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            text=True,
            check=False,
        )
        return result.returncode == 0, (result.stderr or "").strip()
    except OSError as e:
        return False, str(e)
