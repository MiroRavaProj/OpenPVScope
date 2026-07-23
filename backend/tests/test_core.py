"""Tests for project store and alignment math."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openpvscope.alignment.core import estimate_affine
from openpvscope.domain.models import StepStatus
from openpvscope.project.store import ProjectStore
from openpvscope.thermal.dji import ThermalFormat, detect_thermal_format
from openpvscope.workflow import mark_step


def test_create_autosave_reopen(tmp_path: Path) -> None:
    parent = tmp_path / "projects"
    parent.mkdir()
    store = ProjectStore()
    root = store.create("Demo Plant", parent)
    assert root == parent / "Demo_Plant"
    assert (root / "Demo_Plant.opsx").is_file()
    assert store.read_manifest().name == "Demo Plant"
    assert store.read_workflow().photogrammetry.status == StepStatus.ACTIVE

    opsx = store.opsx_path
    assert opsx is not None
    store.close()

    store2 = ProjectStore()
    store2.open_opsx(opsx)
    assert store2.read_manifest().name == "Demo Plant"
    assert (store2.root / "inputs" / "ortho").is_dir()


def test_workflow_autosave(tmp_path: Path) -> None:
    parent = tmp_path / "p"
    parent.mkdir()
    store = ProjectStore()
    store.create("W", parent)
    mark_step(store, "photogrammetry", StepStatus.SKIPPED, skipped=True)
    wf = store.read_workflow()
    assert wf.photogrammetry.status == StepStatus.SKIPPED
    assert wf.alignment.status == StepStatus.ACTIVE
    # Descriptor rewritten
    assert store.opsx_path is not None
    data = store.opsx_path.read_text(encoding="utf-8")
    assert "skipped" in data


def test_mark_step_no_cascade_on_resave(tmp_path: Path) -> None:
    parent = tmp_path / "p2"
    parent.mkdir()
    store = ProjectStore()
    store.create("Cascade", parent)
    mark_step(store, "photogrammetry", StepStatus.SKIPPED, skipped=True)
    mark_step(store, "alignment", StepStatus.DONE, message="first")
    wf = store.read_workflow()
    assert wf.detection.status == StepStatus.ACTIVE
    assert wf.segmentation.status == StepStatus.PENDING

    # Re-saving alignment must not unlock segmentation / models / …
    mark_step(store, "alignment", StepStatus.DONE, message="again")
    mark_step(store, "alignment", StepStatus.DONE, message="again 2")
    wf = store.read_workflow()
    assert wf.detection.status == StepStatus.ACTIVE
    assert wf.segmentation.status == StepStatus.PENDING
    assert wf.models.status == StepStatus.PENDING
    assert wf.classification.status == StepStatus.PENDING
    assert wf.outputs.status == StepStatus.PENDING


def test_export_import_opsz(tmp_path: Path) -> None:
    parent = tmp_path / "src"
    parent.mkdir()
    store = ProjectStore()
    store.create("Pack Me", parent)
    (store.root / "inputs" / "ortho" / "note.txt").write_text("hi", encoding="utf-8")
    (store.root / "work" / "big.bin").write_bytes(b"x" * 10)
    store.autosave()

    opsz = tmp_path / "pack.opsz"
    store.export_opsz(opsz, mode="full")
    assert opsz.is_file()

    light = tmp_path / "pack_light.opsz"
    store.export_opsz(light, mode="light")
    import zipfile

    with zipfile.ZipFile(light) as zf:
        names = zf.namelist()
    assert not any(n.startswith("work/") for n in names)
    assert any(n.endswith("note.txt") for n in names)

    store.close()

    dest = tmp_path / "imported"
    dest.mkdir()
    store2 = ProjectStore()
    store2.import_opsz(opsz, dest)
    assert store2.read_manifest().name == "Pack Me"
    assert (store2.root / "inputs" / "ortho" / "note.txt").read_text(encoding="utf-8") == "hi"


def test_undo_redo_workflow(tmp_path: Path) -> None:
    parent = tmp_path / "h"
    parent.mkdir()
    store = ProjectStore()
    store.create("Hist", parent)
    assert not store.history_status().can_undo

    mark_step(store, "photogrammetry", StepStatus.SKIPPED, skipped=True)
    assert store.history_status().can_undo
    assert store.read_workflow().photogrammetry.status == StepStatus.SKIPPED

    label = store.undo()
    assert label is not None
    assert store.read_workflow().photogrammetry.status == StepStatus.ACTIVE
    assert store.history_status().can_redo

    store.redo()
    assert store.read_workflow().photogrammetry.status == StepStatus.SKIPPED


def test_history_cas_dedup_and_gc(tmp_path: Path, monkeypatch) -> None:
    from openpvscope import settings as settings_mod
    from openpvscope.project.history import ProjectHistory

    monkeypatch.setattr(settings_mod, "config_dir", lambda: tmp_path / "cfg")
    settings_mod.save_settings(
        settings_mod.AppSettings(history_max_steps=2, history_include_rasters=True)
    )

    parent = tmp_path / "cas"
    parent.mkdir()
    store = ProjectStore()
    store.create("CAS", parent)
    ortho = store.root / "inputs" / "ortho"
    ortho.mkdir(parents=True, exist_ok=True)
    big = ortho / "rgb.tif"
    big.write_bytes(b"RASTER" * 50_000)  # ~300KB unique payload

    hist = ProjectHistory(store.root)
    hist.checkpoint("a")
    hist.checkpoint("b")  # same raster bytes → one object
    objects = list((store.root / ".openpvscope_history" / "objects").rglob("*"))
    object_files = [p for p in objects if p.is_file()]
    assert len(object_files) >= 1
    # Unchanged big file must not duplicate
    raster_objs = [p for p in object_files if p.stat().st_size == big.stat().st_size]
    assert len(raster_objs) == 1

    hist.checkpoint("c")  # trim oldest with max_steps=2 → GC
    assert hist.status().depth == 2
    # Still a single raster blob while it remains referenced
    object_files = [
        p for p in (store.root / ".openpvscope_history" / "objects").rglob("*") if p.is_file()
    ]
    raster_objs = [p for p in object_files if p.stat().st_size == big.stat().st_size]
    assert len(raster_objs) == 1

    # Change raster → new object; old may remain if still referenced by a snap
    big.write_bytes(b"CHANGED" * 50_000)
    hist.checkpoint("d")
    object_files = [
        p for p in (store.root / ".openpvscope_history" / "objects").rglob("*") if p.is_file()
    ]
    raster_sizes = {p.stat().st_size for p in object_files if p.stat().st_size > 10_000}
    assert len(raster_sizes) >= 1



def test_atomic_json(tmp_path: Path) -> None:
    from openpvscope.io_atomic import atomic_write_json

    p = tmp_path / "a.json"
    atomic_write_json(p, {"ok": True})
    assert p.read_text(encoding="utf-8").strip().startswith("{")


def test_settings_roundtrip(tmp_path: Path, monkeypatch) -> None:
    from openpvscope import settings as settings_mod

    monkeypatch.setattr(settings_mod, "config_dir", lambda: tmp_path / "cfg")
    s = settings_mod.update_settings({"history_max_steps": 7, "opsz_default_mode": "light"})
    assert s.history_max_steps == 7
    assert settings_mod.load_settings().opsz_default_mode == "light"


def test_create_requires_folder(tmp_path: Path) -> None:
    store = ProjectStore()
    with pytest.raises(ValueError):
        store.create("X", "")


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
