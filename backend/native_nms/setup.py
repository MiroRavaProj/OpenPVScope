"""Build openpvscope.detection._spatial_nms (custom center-bin NMS)."""

from __future__ import annotations

from pathlib import Path

from setuptools import setup

import pybind11
from pybind11.setup_helpers import Pybind11Extension, build_ext

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "openpvscope" / "detection" / "spatial_nms.cpp"

ext_modules = [
    Pybind11Extension(
        "_spatial_nms",
        [str(SRC)],
        include_dirs=[pybind11.get_include()],
        cxx_std=17,
        extra_compile_args=["/O2"] if __import__("sys").platform == "win32" else ["-O3"],
    ),
]

setup(
    name="openpvscope-spatial-nms",
    version="0.1.0",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
    zip_safe=False,
)
