"""Convert one DJI R-JPEG thermal image to float32 TIFF."""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_float, c_int32, c_uint8, c_uint16, c_void_p, cast
from pathlib import Path

import numpy as np
from PIL import Image

from openpvscope.thermal.dji_sdk import (
    DIRP_SUCCESS,
    DIRP_MEASUREMENT_PARAMS,
    DIRP_RESOLUTION,
    require_dji_sdk,
)
from openpvscope.thermal.exiftool_meta import copy_metadata_exiftool
from openpvscope.thermal.parametric import try_parametric_temperature_fallback


def get_thermal_calibration_byte0(rjpeg_data: bytes) -> int | None:
    """Return the first byte of the APP5 thermal calibration segment, if present."""
    i = 2
    data_len = len(rjpeg_data)
    while i < data_len - 3:
        if rjpeg_data[i] != 0xFF:
            i += 1
            continue
        marker = rjpeg_data[i + 1]
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        seglen = int.from_bytes(rjpeg_data[i + 2 : i + 4], "big")
        if marker == 0xE5 and seglen > 2:
            return rjpeg_data[i + 4]
        i += 2 + seglen
    return None


def extended_range_sdk_error(filename: str) -> str:
    return (
        f"{filename}: Extended-range thermal calibration is not supported by the DJI SDK yet. "
        "Re-capture with target distance <=25 m in DJI Pilot on the remote controller."
    )


def _save_float32_tiff(temp_array: np.ndarray, dest_tiff: Path) -> None:
    dest_tiff = Path(dest_tiff)
    dest_tiff.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.asarray(temp_array, dtype=np.float32))
    img.save(dest_tiff, format="TIFF", compression="tiff_lzw")


def process_rjpeg_to_float_tiff(
    source: Path,
    dest_tiff: Path,
    *,
    emissivity: float = 0.95,
    distance: float = 5.0,
    humidity: float = 50.0,
    reflection: float = 25.0,
    ambient_temp: float = 25.0,
    parametric_fallback: bool = False,
    parametric_model_path: Path | str | None = None,
    copy_metadata: bool = True,
) -> Path:
    """
    Convert a DJI R-JPEG to a single-band float32 TIFF (Celsius).

    Raises:
        FileNotFoundError: source missing
        ValueError: visible-light companion (_V.) or not a thermal R-JPEG
        RuntimeError: SDK missing / measurement failure
    """
    source = Path(source)
    dest_tiff = Path(dest_tiff)
    filename = source.name

    if "_V." in filename:
        raise ValueError(f"{filename}: skipped (visible light image, not thermal)")

    if not source.is_file():
        raise FileNotFoundError(f"Thermal source not found: {source}")

    libdirp = require_dji_sdk()
    rjpeg_data = source.read_bytes()
    calib_byte0 = get_thermal_calibration_byte0(rjpeg_data)

    rjpeg_data_ptr = ctypes.create_string_buffer(rjpeg_data)
    rjpeg_size = len(rjpeg_data)
    dirp_handle = c_void_p()
    ret = libdirp.dirp_create_from_rjpeg(
        cast(rjpeg_data_ptr, POINTER(c_uint8)),
        c_int32(rjpeg_size),
        byref(dirp_handle),
    )
    if ret != DIRP_SUCCESS:
        if ret == -7:
            raise ValueError(f"{filename}: not a thermal image (SDK code {ret})")
        raise RuntimeError(f"{filename}: failed SDK handle creation, code {ret}")

    try:
        resolution = DIRP_RESOLUTION()
        ret = libdirp.dirp_get_rjpeg_resolution(dirp_handle, byref(resolution))
        if ret != DIRP_SUCCESS:
            raise RuntimeError(f"{filename}: failed get resolution, code {ret}")

        width, height = int(resolution.width), int(resolution.height)

        params = DIRP_MEASUREMENT_PARAMS()
        params.distance = c_float(float(distance))
        params.humidity = c_float(float(humidity))
        params.emissivity = c_float(float(emissivity))
        params.reflection = c_float(float(reflection))
        params.ambient_temp = c_float(float(ambient_temp))

        ret = libdirp.dirp_set_measurement_params(dirp_handle, byref(params))
        if ret != DIRP_SUCCESS:
            raise RuntimeError(f"{filename}: failed set params, code {ret}")

        output_size = width * height * 4
        temp_buffer = (c_float * (width * height))()
        ret = libdirp.dirp_measure_ex(dirp_handle, temp_buffer, c_int32(output_size))

        if ret != DIRP_SUCCESS:
            temp_array = None
            if parametric_fallback:
                raw_buffer = (c_uint16 * (width * height))()
                ret_raw = libdirp.dirp_get_original_raw(
                    dirp_handle, raw_buffer, c_int32(width * height * 2)
                )
                if ret_raw == DIRP_SUCCESS:
                    raw_array = (
                        np.frombuffer(raw_buffer, dtype=np.uint16)
                        .reshape(height, width)
                        .copy()
                    )
                    temp_array = try_parametric_temperature_fallback(
                        raw_array,
                        emissivity,
                        distance,
                        humidity,
                        reflection,
                        parametric_model_path=parametric_model_path,
                        calib_byte0=calib_byte0,
                    )

            if temp_array is None:
                if ret == -12 and calib_byte0 == 0:
                    raise RuntimeError(extended_range_sdk_error(filename))
                raise RuntimeError(f"{filename}: failed extract temp data, code {ret}")
        else:
            temp_array = np.frombuffer(temp_buffer, dtype=np.float32).reshape(height, width).copy()
    finally:
        libdirp.dirp_destroy(dirp_handle)

    _save_float32_tiff(temp_array, dest_tiff)

    if copy_metadata:
        copy_metadata_exiftool(source, dest_tiff)

    return dest_tiff
