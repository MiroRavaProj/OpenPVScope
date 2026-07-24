"""Thermal-only workflow, detection ortho fallback, and segmentation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from openpvscope.domain.models import StepStatus
from openpvscope.photogrammetry.setup import save_setup
from openpvscope.project.paths import ortho_thermal
from openpvscope.project.store import ProjectStore
from openpvscope.workflow import (
    mark_step,
    orthos_ready,
    skip_alignment_for_thermal_only,
    skip_photogrammetry_with_geotiffs,
)


def _write_dummy_geotiff(path: Path, *, bands: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = 32, 32
    transform = from_origin(10.0, 45.0, 0.0001, 0.0001)
    data = np.full((bands, h, w), 25.0, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=bands,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data)


def _new_store(tmp_path: Path, name: str) -> ProjectStore:
    parent = tmp_path / "projects"
    parent.mkdir(exist_ok=True)
    store = ProjectStore()
    store.create(name, parent)
    return store


def test_orthos_ready_thermal_only(tmp_path: Path) -> None:
    store = _new_store(tmp_path, "T")
    save_setup(store.root, {"wizard_complete": True, "modalities": "thermal_only", "mode": "skip"})
    assert orthos_ready(store) is False
    _write_dummy_geotiff(ortho_thermal(store.root))
    assert orthos_ready(store) is True


def test_skip_geotiff_thermal_only_unlocks_detection(tmp_path: Path) -> None:
    store = _new_store(tmp_path, "T2")
    save_setup(store.root, {"wizard_complete": True, "modalities": "thermal_only", "mode": "skip"})
    th = tmp_path / "th.tif"
    _write_dummy_geotiff(th)
    skip_photogrammetry_with_geotiffs(store, None, th)
    wf = store.read_workflow()
    assert wf.get("photogrammetry").status == StepStatus.DONE
    assert wf.get("alignment").status == StepStatus.SKIPPED
    assert wf.get("detection").status == StepStatus.ACTIVE


def test_skip_alignment_helper(tmp_path: Path) -> None:
    store = _new_store(tmp_path, "T3")
    mark_step(store, "photogrammetry", StepStatus.DONE, message="done")
    skip_alignment_for_thermal_only(store)
    wf = store.read_workflow()
    assert wf.get("alignment").status == StepStatus.SKIPPED
    assert wf.get("detection").status == StepStatus.ACTIVE


def test_ortho_for_thermal_falls_back_to_raw(tmp_path: Path) -> None:
    from openpvscope.detection import pipeline as det

    store = _new_store(tmp_path, "T4")
    save_setup(store.root, {"wizard_complete": True, "modalities": "thermal_only", "mode": "process"})
    _write_dummy_geotiff(ortho_thermal(store.root))
    path = det._ortho_for(store.root, "thermal")
    assert path == ortho_thermal(store.root)
    assert path.is_file()


def test_segmentation_thermal_only(tmp_path: Path) -> None:
    from openpvscope.detection.pipeline import detection_dir
    from openpvscope.geo.crs import feature_collection, polygon_feature
    from openpvscope.io_atomic import atomic_write_json
    from openpvscope.segmentation.extract import run_segmentation, segmentation_status

    store = _new_store(tmp_path, "T5")
    save_setup(store.root, {"wizard_complete": True, "modalities": "thermal_only", "mode": "skip"})
    _write_dummy_geotiff(ortho_thermal(store.root))

    ring = [
        [10.00005, 44.99995],
        [10.00015, 44.99995],
        [10.00015, 44.99985],
        [10.00005, 44.99985],
        [10.00005, 44.99995],
    ]
    panels = feature_collection(
        [polygon_feature(ring, {"kind": "panel", "confidence": 0.9}, fid="p1")],
        name="panels",
    )
    ddir = detection_dir(store.root, "thermal")
    atomic_write_json(ddir / "panels.geojson", panels)

    result = run_segmentation(store.root, margin_factor=0.1)
    assert result["mode"] == "thermal_only"
    assert result["count"] >= 1
    panel_dir = store.root / "segmentation" / "panels" / "p1"
    assert (panel_dir / "thermal.png").is_file()
    assert (panel_dir / "thermal.tif").is_file()
    assert not (panel_dir / "rgb.png").is_file()
    st = segmentation_status(store.root)
    assert "thermal" in st["message"].lower()
    assert st["pair_count"] >= 1


def test_detection_status_thermal_only_messages(tmp_path: Path) -> None:
    from openpvscope.detection.pipeline import detection_dir, detection_status, save_aoi_geojson
    from openpvscope.io_atomic import atomic_write_json

    store = _new_store(tmp_path, "T6")
    save_setup(store.root, {"wizard_complete": True, "modalities": "thermal_only", "mode": "skip"})
    st = detection_status(store.root)
    assert st["has_aoi"] is False
    assert "thermal" in st["message"].lower()
    assert "rgb" not in st["message"].lower()

    ring = [
        [10.0, 45.0],
        [10.001, 45.0],
        [10.001, 44.999],
        [10.0, 44.999],
    ]
    save_aoi_geojson(store.root, ring, modality="thermal")
    st = detection_status(store.root)
    assert st["has_aoi"] is True
    assert st["thermal"]["has_aoi"] is True
    assert "generate grid" in st["message"].lower()

    atomic_write_json(detection_dir(store.root, "thermal") / "grid.geojson", {"type": "FeatureCollection", "features": []})
    st = detection_status(store.root)
    assert st["has_grid"] is True
    assert st["both_grids_ready"] is True
    assert "run thermal" in st["message"].lower()
