"""Tests for .opsx store and alignment math."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openpvscope.alignment.core import estimate_affine
from openpvscope.domain.models import StepStatus
from openpvscope.project.store import ProjectStore
from openpvscope.thermal.dji import ThermalFormat, detect_thermal_format
from openpvscope.workflow import mark_step


def test_create_save_open_opsx(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    opsx = tmp_path / "demo.opsx"
    store = ProjectStore(cache_root=cache)
    store.create("Demo Plant", opsx)
    assert opsx.is_file()
    assert store.read_manifest().name == "Demo Plant"
    assert store.read_workflow().photogrammetry.status == StepStatus.ACTIVE

    store.close()
    store2 = ProjectStore(cache_root=cache / "other")
    store2.open_opsx(opsx)
    assert store2.read_manifest().name == "Demo Plant"
    assert (store2.root / "inputs" / "ortho").is_dir()


def test_workflow_advance(tmp_path: Path) -> None:
    store = ProjectStore(cache_root=tmp_path / "c")
    store.create("W")
    mark_step(store, "photogrammetry", StepStatus.SKIPPED, skipped=True)
    wf = store.read_workflow()
    assert wf.photogrammetry.status == StepStatus.SKIPPED
    assert wf.alignment.status == StepStatus.ACTIVE


def test_estimate_affine_identity() -> None:
    pts = [[0, 0], [10, 0], [10, 10], [0, 10]]
    M = estimate_affine(pts, pts)
    assert M.shape == (3, 3)
    np.testing.assert_allclose(M, np.eye(3), atol=1e-6)


def test_detect_tiff(tmp_path: Path) -> None:
    p = tmp_path / "a.tif"
    p.write_bytes(b"II*\x00")
    assert detect_thermal_format(p) == ThermalFormat.TIFF


def test_detect_jpeg_as_dji(tmp_path: Path) -> None:
    p = tmp_path / "t.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert detect_thermal_format(p) == ThermalFormat.DJI_PROPRIETARY
