"""FastAPI application for OpenPVScope."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from openpvscope import __version__
from openpvscope.alignment import apply_georef_rewrite, save_alignment_artifacts
from openpvscope.detection import detection_status
from openpvscope.domain.models import PIPELINE_STEPS, StepStatus
from openpvscope.exports import exports_status
from openpvscope.ingest import create_preview_png, inspect_geotiff
from openpvscope.ml import ml_status
from openpvscope.opensfm import OpenSfMRunner, find_opensfm_root, probe_opencl
from openpvscope.project import get_store
from openpvscope.project.paths import ortho_rgb, ortho_thermal, ortho_thermal_aligned
from openpvscope.segmentation import segmentation_status
from openpvscope.thermal import detect_thermal_format
from openpvscope.workflow import mark_step, orthos_ready, skip_photogrammetry_with_geotiffs

app = FastAPI(title="OpenPVScope", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_sfm_logs: dict[str, list[str]] = {"rgb": [], "thermal": []}
_sfm_thread: threading.Thread | None = None


class CreateProjectBody(BaseModel):
    name: str = Field(min_length=1)
    opsx_path: str | None = None


class OpenProjectBody(BaseModel):
    path: str


class AlignBody(BaseModel):
    ref_points: list[list[float]]
    target_points: list[list[float]]


class SkipPhotoBody(BaseModel):
    rgb_path: str
    thermal_path: str


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "opensfm_root": str(find_opensfm_root()) if find_opensfm_root() else None,
        "opencl": probe_opencl(),
    }


@app.get("/api/pipeline")
def pipeline_steps() -> dict[str, Any]:
    return {"steps": list(PIPELINE_STEPS)}


@app.post("/api/projects")
def create_project(body: CreateProjectBody) -> dict[str, Any]:
    store = get_store()
    opsx = Path(body.opsx_path) if body.opsx_path else None
    root = store.create(body.name, opsx)
    return _project_payload(store)


@app.post("/api/projects/open")
def open_project(body: OpenProjectBody) -> dict[str, Any]:
    store = get_store()
    path = Path(body.path)
    try:
        if path.suffix.lower() == ".opsx":
            store.open_opsx(path)
        else:
            store.open_directory(path)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return _project_payload(store)


@app.get("/api/projects/current")
def current_project() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    return _project_payload(store)


@app.post("/api/projects/save")
def save_project(opsx_path: str | None = None) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = store.save(Path(opsx_path) if opsx_path else None)
    return {"opsx_path": str(path), **_project_payload(store)}


def _project_payload(store) -> dict[str, Any]:
    manifest = store.read_manifest()
    workflow = store.read_workflow()
    root = store.root
    layers = []
    for key, path in (
        ("rgb", ortho_rgb(root)),
        ("thermal", ortho_thermal(root)),
        ("thermal_aligned", ortho_thermal_aligned(root)),
    ):
        if path.is_file():
            try:
                info = inspect_geotiff(path)
                layers.append({"id": key, **info})
            except Exception as e:
                layers.append({"id": key, "path": str(path), "error": str(e)})
    return {
        "manifest": manifest.model_dump(mode="json"),
        "workflow": workflow.model_dump(mode="json"),
        "root": str(root),
        "opsx_path": str(store.opsx_path) if store.opsx_path else None,
        "orthos_ready": orthos_ready(store),
        "layers": layers,
    }


@app.post("/api/photogrammetry/skip")
async def skip_photogrammetry(
    rgb: UploadFile = File(...),
    thermal: UploadFile = File(...),
) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    tmp = store.root / "work" / "uploads"
    tmp.mkdir(parents=True, exist_ok=True)
    rgb_path = tmp / (rgb.filename or "rgb.tif")
    thermal_path = tmp / (thermal.filename or "thermal.tif")
    with rgb_path.open("wb") as f:
        shutil.copyfileobj(rgb.file, f)
    with thermal_path.open("wb") as f:
        shutil.copyfileobj(thermal.file, f)
    skip_photogrammetry_with_geotiffs(store, rgb_path, thermal_path)
    _refresh_overlays(store)
    return _project_payload(store)


@app.post("/api/photogrammetry/skip-paths")
def skip_photogrammetry_paths(body: SkipPhotoBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    skip_photogrammetry_with_geotiffs(store, body.rgb_path, body.thermal_path)
    _refresh_overlays(store)
    return _project_payload(store)


@app.post("/api/photogrammetry/upload-raw/{modality}")
async def upload_raw(modality: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if modality not in ("rgb", "thermal"):
        raise HTTPException(400, "modality must be rgb or thermal")
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    dest_dir = store.root / "inputs" / "raw" / modality
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    formats = []
    for uf in files:
        name = uf.filename or "image.bin"
        path = dest_dir / name
        with path.open("wb") as f:
            shutil.copyfileobj(uf.file, f)
        saved.append(str(path))
        if modality == "thermal":
            formats.append({"path": str(path), "format": detect_thermal_format(path).value})
    return {"saved": saved, "formats": formats, "count": len(saved)}


@app.get("/api/photogrammetry/opencl")
def opencl_status() -> dict[str, Any]:
    return probe_opencl()


@app.post("/api/photogrammetry/run/{modality}")
def run_photogrammetry(modality: str) -> dict[str, Any]:
    global _sfm_thread
    if modality not in ("rgb", "thermal"):
        raise HTTPException(400, "modality must be rgb or thermal")
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")

    raw_dir = store.root / "inputs" / "raw" / modality
    images = sorted(
        p
        for p in raw_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".rjpeg"}
    )
    if not images:
        raise HTTPException(400, f"No images in inputs/raw/{modality}")

    runner = OpenSfMRunner(store.root)
    runner.prepare_dataset(modality, images)
    _sfm_logs[modality] = []

    def work() -> None:
        def on_log(line: str) -> None:
            _sfm_logs[modality].append(line)

        try:
            runner.run(modality, on_log=on_log)
            if modality == "thermal" and ortho_rgb(store.root).is_file():
                mark_step(store, "photogrammetry", StepStatus.DONE, message="OpenSfM complete")
            elif modality == "rgb" and ortho_thermal(store.root).is_file():
                mark_step(store, "photogrammetry", StepStatus.DONE, message="OpenSfM complete")
            _refresh_overlays(store)
        except Exception as e:
            _sfm_logs[modality].append(f"ERROR: {e}")

    if _sfm_thread and _sfm_thread.is_alive():
        raise HTTPException(409, "A photogrammetry job is already running")
    _sfm_thread = threading.Thread(target=work, daemon=True)
    _sfm_thread.start()
    return {"started": True, "modality": modality}


@app.get("/api/photogrammetry/status/{modality}")
def photogrammetry_status(modality: str) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    runner = OpenSfMRunner(store.root)
    job = runner.get_job(modality)
    ortho = store.root / "inputs" / "ortho" / f"{modality}.tif"
    return {
        "job": job.__dict__ if job else None,
        "log": _sfm_logs.get(modality, [])[-200:],
        "ortho_exists": ortho.is_file(),
        "running": bool(_sfm_thread and _sfm_thread.is_alive()),
    }


def _refresh_overlays(store) -> None:
    root = store.root
    overlay_dir = root / "work" / "overlays"
    for key, path in (("rgb", ortho_rgb(root)), ("thermal", ortho_thermal(root)), ("thermal_aligned", ortho_thermal_aligned(root))):
        if path.is_file():
            try:
                create_preview_png(path, overlay_dir / f"{key}.png")
            except Exception:
                pass


@app.get("/api/map/layers")
def map_layers() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    _refresh_overlays(store)
    overlay_dir = store.root / "work" / "overlays"
    layers = []
    for key, path in (
        ("rgb", ortho_rgb(store.root)),
        ("thermal", ortho_thermal_aligned(store.root) if ortho_thermal_aligned(store.root).is_file() else ortho_thermal(store.root)),
    ):
        if not path.is_file():
            continue
        png = overlay_dir / f"{'thermal_aligned' if key == 'thermal' and ortho_thermal_aligned(store.root).is_file() else key}.png"
        if key == "thermal" and ortho_thermal_aligned(store.root).is_file():
            info_path = ortho_thermal_aligned(store.root)
            png = overlay_dir / "thermal_aligned.png"
            if not png.is_file():
                create_preview_png(info_path, png)
        else:
            info_path = path
            png = overlay_dir / f"{key}.png"
            if not png.is_file():
                create_preview_png(info_path, png)
        meta = inspect_geotiff(info_path)
        layers.append(
            {
                "id": key,
                "png_url": f"/api/map/overlay/{png.name}",
                "bounds": meta["bounds"],
                "crs": meta["crs"],
            }
        )
    return {"layers": layers}


@app.get("/api/map/overlay/{name}")
def map_overlay(name: str):
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = store.root / "work" / "overlays" / name
    if not path.is_file():
        raise HTTPException(404, "Overlay not found")
    return FileResponse(path, media_type="image/png")


@app.post("/api/alignment/apply")
def alignment_apply(body: AlignBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    rgb = ortho_rgb(store.root)
    thermal = ortho_thermal(store.root)
    if not rgb.is_file() or not thermal.is_file():
        raise HTTPException(400, "RGB and thermal orthophotos required")
    out = ortho_thermal_aligned(store.root)
    try:
        result = apply_georef_rewrite(
            rgb, thermal, out, body.target_points, body.ref_points
        )
        save_alignment_artifacts(store.root, body.ref_points, body.target_points, result)
        mark_step(store, "alignment", StepStatus.DONE, message="Thermal georef aligned to RGB")
        _refresh_overlays(store)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"result": result, **_project_payload(store)}


@app.get("/api/detection/status")
def api_detection_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    return detection_status(store.root)


@app.get("/api/segmentation/status")
def api_segmentation_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    return segmentation_status(store.root)


@app.get("/api/ml/status")
def api_ml_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    return ml_status(store.root)


@app.get("/api/exports/status")
def api_exports_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    return exports_status(store.root)


# Serve built frontend if present
_STATIC = Path(__file__).resolve().parent.parent / "static"
if _STATIC.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
