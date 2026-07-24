"""DJI Thermal SDK (libdirp) discovery and ctypes bindings."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import (
    POINTER,
    Structure,
    c_float,
    c_int32,
    c_uint8,
    c_uint16,
    c_void_p,
)
from pathlib import Path

DIRP_SUCCESS = 0

ENV_DJI_TSDK = "OPENPVSCOPE_DJI_TSDK"


class DIRP_RESOLUTION(Structure):
    _fields_ = [
        ("width", c_int32),
        ("height", c_int32),
    ]


class DIRP_MEASUREMENT_PARAMS(Structure):
    _fields_ = [
        ("distance", c_float),
        ("humidity", c_float),
        ("emissivity", c_float),
        ("reflection", c_float),
        ("ambient_temp", c_float),
    ]


def _candidate_sdk_roots() -> list[Path]:
    candidates: list[Path] = []

    env = os.environ.get(ENV_DJI_TSDK)
    if env:
        candidates.append(Path(env))

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "engines" / "dji_tsdk")
        candidates.append(exe_dir.parent / "engines" / "dji_tsdk")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "engines" / "dji_tsdk")

    here = Path(__file__).resolve()
    try:
        # .../backend/openpvscope/thermal/dji_sdk.py -> OpenPVScope/engines/dji_tsdk
        candidates.append(here.parents[3] / "engines" / "dji_tsdk")
    except IndexError:
        pass
    try:
        # Installed package layout fallback
        candidates.append(here.parents[2] / "engines" / "dji_tsdk")
    except IndexError:
        pass
    candidates.append(Path.cwd() / "engines" / "dji_tsdk")

    # Deduplicate while preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for c in candidates:
        try:
            key = c.resolve()
        except OSError:
            key = c
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def find_dji_tsdk_root() -> Path | None:
    """Return SDK root that contains libdirp.dll, or None."""
    for root in _candidate_sdk_roots():
        dll = root / "libdirp.dll"
        nested = root / "DJI_TSDK" / "libdirp.dll"
        if dll.is_file():
            return root
        if nested.is_file():
            return root / "DJI_TSDK"
    return None


def libdirp_path(root: Path | None = None) -> Path | None:
    root = root or find_dji_tsdk_root()
    if root is None:
        return None
    dll = root / "libdirp.dll"
    return dll if dll.is_file() else None


def _bind_signatures(libdirp: ctypes.CDLL) -> None:
    libdirp.dirp_create_from_rjpeg.argtypes = [POINTER(c_uint8), c_int32, POINTER(c_void_p)]
    libdirp.dirp_create_from_rjpeg.restype = c_int32

    libdirp.dirp_get_rjpeg_resolution.argtypes = [c_void_p, POINTER(DIRP_RESOLUTION)]
    libdirp.dirp_get_rjpeg_resolution.restype = c_int32

    libdirp.dirp_set_measurement_params.argtypes = [
        c_void_p,
        POINTER(DIRP_MEASUREMENT_PARAMS),
    ]
    libdirp.dirp_set_measurement_params.restype = c_int32

    libdirp.dirp_measure_ex.argtypes = [c_void_p, POINTER(c_float), c_int32]
    libdirp.dirp_measure_ex.restype = c_int32

    libdirp.dirp_destroy.argtypes = [c_void_p]
    libdirp.dirp_destroy.restype = c_int32

    libdirp.dirp_get_original_raw.argtypes = [c_void_p, POINTER(c_uint16), c_int32]
    libdirp.dirp_get_original_raw.restype = c_int32


_libdirp: ctypes.CDLL | None = None
_libdirp_error: str | None = None
_libdirp_root: Path | None = None


def load_dji_thermal_sdk(*, force: bool = False) -> ctypes.CDLL | None:
    """Load libdirp.dll and bind DIRP function signatures. Cached."""
    global _libdirp, _libdirp_error, _libdirp_root

    if _libdirp is not None and not force:
        return _libdirp
    if _libdirp_error is not None and not force:
        return None

    root = find_dji_tsdk_root()
    dll = libdirp_path(root)
    if dll is None:
        _libdirp_error = (
            "DJI Thermal SDK (libdirp.dll) not found. "
            f"Set {ENV_DJI_TSDK} or place the SDK under engines/dji_tsdk/. "
            "See engines/dji_tsdk/README.md."
        )
        _libdirp = None
        _libdirp_root = None
        return None

    try:
        # Ensure dependent DLLs in the same folder resolve on Windows.
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(dll.parent))
        lib = ctypes.CDLL(str(dll))
        _bind_signatures(lib)
        _libdirp = lib
        _libdirp_root = dll.parent
        _libdirp_error = None
        return lib
    except OSError as e:
        _libdirp = None
        _libdirp_root = None
        _libdirp_error = f"Failed to load DJI Thermal SDK at {dll}: {e}"
        return None


def probe_dji_sdk() -> dict:
    """Best-effort DJI Thermal SDK availability check."""
    root = find_dji_tsdk_root()
    dll = libdirp_path(root)
    lib = load_dji_thermal_sdk()
    if lib is not None:
        return {
            "available": True,
            "path": str(_libdirp_root or (dll.parent if dll else root)),
            "dll": str(dll) if dll else None,
            "error": None,
        }
    return {
        "available": False,
        "path": str(root) if root else None,
        "dll": str(dll) if dll else None,
        "error": _libdirp_error
        or (
            f"DJI Thermal SDK not found. Set {ENV_DJI_TSDK} or install under engines/dji_tsdk/."
        ),
    }


def require_dji_sdk() -> ctypes.CDLL:
    lib = load_dji_thermal_sdk()
    if lib is None:
        raise RuntimeError(_libdirp_error or "DJI Thermal SDK not available.")
    return lib
