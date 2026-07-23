"""FastAPI application for OpenPVScope."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from openpvscope import __version__
from openpvscope.alignment import apply_georef_rewrite, save_alignment_artifacts
from openpvscope.console import get_console
from openpvscope.detection import (
    clear_detection,
    copy_rgb_grid_to_thermal,
    detection_job_status,
    detection_status,
    generate_grid,
    load_geojson,
    save_aoi_geojson,
    start_detection_job,
)
from openpvscope.detection.pipeline import detection_dir
from openpvscope.domain.models import PIPELINE_STEPS, StepStatus
from openpvscope.exports import exports_status
from openpvscope.geo.crs import bounds_to_wgs84
from openpvscope.ingest import (
    create_preview_png,
    estimate_maxzoom,
    inspect_geotiff,
    render_geotiff_geo_window,
    render_geotiff_matched_to_ref_window,
    render_geotiff_window,
    render_geotiff_xyz_tile,
)
from openpvscope.ml import ml_status
from openpvscope.opensfm import OpenSfMRunner, find_opensfm_root, probe_opencl
from openpvscope.opensfm.runner import OPENSFM_COMMANDS
from openpvscope.project import get_store
from openpvscope.project.paths import ortho_rgb, ortho_thermal, ortho_thermal_aligned
from openpvscope.segmentation import (
    segmentation_job_status,
    segmentation_status,
    start_segmentation_job,
)
from openpvscope.segmentation.extract import segmentation_root
from openpvscope.settings import (
    clear_recent_projects,
    load_settings,
    update_settings,
)
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
    project_dir: str = Field(min_length=1, description="Parent folder where the project will be created")


class OpenProjectBody(BaseModel):
    path: str


class ExportOpszBody(BaseModel):
    dest_path: str
    mode: str | None = Field(default=None, description="full | light; defaults to settings")


class ImportOpszBody(BaseModel):
    opsz_path: str
    dest_dir: str


class AlignBody(BaseModel):
    ref_points: list[list[float]]
    target_points: list[list[float]]


class SkipPhotoBody(BaseModel):
    rgb_path: str
    thermal_path: str


class SettingsPatch(BaseModel):
    history_max_steps: int | None = None
    history_include_rasters: bool | None = None
    default_project_dir: str | None = None
    recent_max: int | None = None
    opsz_default_mode: str | None = None
    opsz_light_exclude: list[str] | None = None
    clear_default_project_dir: bool = False
    clear_recent: bool = False


class AoiBody(BaseModel):
    ring: list[list[float]] = Field(description="4 corners [[lon,lat], ...]")
    modality: Literal["rgb", "thermal"] = "rgb"
    regenerate_grid: bool = False


class GridBody(BaseModel):
    rows: int = Field(ge=1, le=200)
    cols: int = Field(ge=1, le=200)
    modality: Literal["rgb", "thermal"] = "rgb"


class DetectRunBody(BaseModel):
    confidence: float = Field(default=0.5, ge=0.1, le=0.99)
    nms_iou: float = Field(default=0.05, ge=0.01, le=0.9)
    num_templates: int = Field(default=1, ge=1, le=50)
    modality: Literal["rgb", "thermal", "both"] = "both"


class SegmentRunBody(BaseModel):
    margin_factor: float = Field(default=0.15, ge=0.0, le=1.0)


def _pick_with_tk(mode: str) -> str | None:
    """Native Windows dialog via tkinter (desktop / local API)."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if mode == "dir":
            path = filedialog.askdirectory(title="Select folder")
        elif mode == "opsx":
            path = filedialog.askopenfilename(
                title="Open OpenPVScope project",
                filetypes=[("OpenPVScope project", "*.opsx"), ("All files", "*.*")],
            )
        elif mode == "opsz_open":
            path = filedialog.askopenfilename(
                title="Import OpenPVScope archive",
                filetypes=[("OpenPVScope archive", "*.opsz"), ("ZIP", "*.zip"), ("All files", "*.*")],
            )
        elif mode == "opsz_save":
            path = filedialog.asksaveasfilename(
                title="Export OpenPVScope archive",
                defaultextension=".opsz",
                filetypes=[("OpenPVScope archive", "*.opsz")],
            )
        else:
            path = ""
        root.destroy()
        return path or None
    except Exception:
        return None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "opensfm_root": str(find_opensfm_root()) if find_opensfm_root() else None,
        "opencl": probe_opencl(),
    }


@app.get("/api/console")
def console_snapshot(since: int = Query(0, ge=0)) -> dict[str, Any]:
    return get_console().snapshot(since=since)


@app.post("/api/console/clear")
def console_clear() -> dict[str, Any]:
    get_console().clear()
    return {"cleared": True}


@app.post("/api/system/pick-directory")
def pick_directory() -> dict[str, Any]:
    return {"path": _pick_with_tk("dir")}


@app.post("/api/system/pick-opsx")
def pick_opsx() -> dict[str, Any]:
    return {"path": _pick_with_tk("opsx")}


@app.post("/api/system/pick-opsz-open")
def pick_opsz_open() -> dict[str, Any]:
    return {"path": _pick_with_tk("opsz_open")}


@app.post("/api/system/pick-opsz-save")
def pick_opsz_save() -> dict[str, Any]:
    return {"path": _pick_with_tk("opsz_save")}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return load_settings().model_dump(mode="json")


@app.put("/api/settings")
def put_settings(body: SettingsPatch) -> dict[str, Any]:
    patch: dict[str, Any] = body.model_dump(exclude_unset=True)
    clear_recent = patch.pop("clear_recent", False)
    clear_default = patch.pop("clear_default_project_dir", False)
    # Drop None values except when clearing default dir
    clean = {k: v for k, v in patch.items() if v is not None}
    if clear_default:
        clean["default_project_dir"] = None
    if clean:
        settings = update_settings(clean)
    else:
        settings = load_settings()
    if clear_recent:
        settings = clear_recent_projects()
    return settings.model_dump(mode="json")


@app.get("/api/projects/recent")
def recent_projects() -> dict[str, Any]:
    s = load_settings()
    # Drop missing files from the list returned (keep settings until user opens/clears)
    items = []
    for r in s.recent_projects:
        exists = Path(r.path).is_file()
        items.append({**r.model_dump(), "exists": exists})
    return {"recent": items}


@app.get("/api/pipeline")
def pipeline_steps() -> dict[str, Any]:
    return {"steps": list(PIPELINE_STEPS)}


@app.post("/api/projects")
def create_project(body: CreateProjectBody) -> dict[str, Any]:
    store = get_store()
    try:
        store.create(body.name, Path(body.project_dir))
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return _project_payload(store)


@app.post("/api/projects/open")
def open_project(body: OpenProjectBody) -> dict[str, Any]:
    store = get_store()
    path = Path(body.path)
    try:
        if path.is_dir():
            store.open_directory(path)
        else:
            store.open_opsx(path)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return _project_payload(store)


@app.get("/api/projects/current")
def current_project() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    return _project_payload(store)


@app.post("/api/projects/autosave")
def autosave_project() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = store.autosave()
    return {"opsx_path": str(path), **_project_payload(store)}


@app.post("/api/projects/save")
def save_project(opsx_path: str | None = None) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = store.save(Path(opsx_path) if opsx_path else None)
    return {"opsx_path": str(path), **_project_payload(store)}


@app.post("/api/projects/export-opsz")
def export_opsz(body: ExportOpszBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    mode = body.mode
    if mode is not None and mode not in ("full", "light"):
        raise HTTPException(400, "mode must be 'full' or 'light'")
    try:
        dest = store.export_opsz(Path(body.dest_path), mode=mode)  # type: ignore[arg-type]
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"opsz_path": str(dest), "mode": mode or load_settings().opsz_default_mode, **_project_payload(store)}


@app.post("/api/projects/import-opsz")
def import_opsz(body: ImportOpszBody) -> dict[str, Any]:
    store = get_store()
    try:
        store.import_opsz(Path(body.opsz_path), Path(body.dest_dir))
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return _project_payload(store)


@app.post("/api/projects/close")
def close_project() -> dict[str, Any]:
    store = get_store()
    if store.is_open:
        store.close()
    return {"closed": True}


@app.get("/api/projects/history")
def history_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    st = store.history_status()
    return {
        "can_undo": st.can_undo,
        "can_redo": st.can_redo,
        "undo_label": st.undo_label,
        "redo_label": st.redo_label,
        "depth": st.depth,
        "redo_depth": st.redo_depth,
    }


@app.post("/api/projects/history/undo")
def history_undo() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    label = store.undo()
    if label is None:
        raise HTTPException(400, "Nothing to undo")
    return {"undone": label, **_project_payload(store)}


@app.post("/api/projects/history/redo")
def history_redo() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    label = store.redo()
    if label is None:
        raise HTTPException(400, "Nothing to redo")
    return {"redone": label, **_project_payload(store)}


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
    hist = store.history_status()
    return {
        "manifest": manifest.model_dump(mode="json"),
        "workflow": workflow.model_dump(mode="json"),
        "root": str(root),
        "opsx_path": str(store.opsx_path) if store.opsx_path else None,
        "orthos_ready": orthos_ready(store),
        "layers": layers,
        "history": {
            "can_undo": hist.can_undo,
            "can_redo": hist.can_redo,
            "undo_label": hist.undo_label,
            "redo_label": hist.redo_label,
            "depth": hist.depth,
            "redo_depth": hist.redo_depth,
        },
    }


@app.post("/api/photogrammetry/skip")
async def skip_photogrammetry(
    rgb: UploadFile = File(...),
    thermal: UploadFile = File(...),
) -> dict[str, Any]:
    store = get_store()
    console = get_console()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    console.begin_job("Import GeoTIFFs", detail="Uploading orthophotos")
    try:
        tmp = store.root / "work" / "uploads"
        tmp.mkdir(parents=True, exist_ok=True)
        rgb_path = tmp / (rgb.filename or "rgb.tif")
        thermal_path = tmp / (thermal.filename or "thermal.tif")
        console.set_progress(20, detail="Saving RGB GeoTIFF", step="rgb")
        with rgb_path.open("wb") as f:
            shutil.copyfileobj(rgb.file, f)
        console.set_progress(55, detail="Saving thermal GeoTIFF", step="thermal")
        with thermal_path.open("wb") as f:
            shutil.copyfileobj(thermal.file, f)
        console.set_progress(80, detail="Updating project", step="workflow")
        skip_photogrammetry_with_geotiffs(store, rgb_path, thermal_path)
        _refresh_overlays(store)
        store.autosave()
        console.end_job(ok=True, message="GeoTIFFs imported — continue to ortho alignment")
    except Exception as e:
        console.end_job(ok=False, message=str(e))
        raise
    return _project_payload(store)


@app.post("/api/photogrammetry/skip-paths")
def skip_photogrammetry_paths(body: SkipPhotoBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    skip_photogrammetry_with_geotiffs(store, body.rgb_path, body.thermal_path)
    _refresh_overlays(store)
    store.autosave()
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
    console = get_console()
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
    n_cmds = len(OPENSFM_COMMANDS) + 1  # + dense_merging

    def work() -> None:
        console.begin_job(f"OpenSfM ({modality})", detail="Starting pipeline")
        step_i = 0

        def on_log(line: str) -> None:
            nonlocal step_i
            _sfm_logs[modality].append(line)
            level = "verbose"
            if line.startswith(">>>"):
                level = "info"
                step_i += 1
                pct = min(95.0, (step_i / max(1, n_cmds)) * 100.0)
                console.set_progress(pct, detail=line.replace(">>> ", ""), step=f"opensfm/{modality}", level="info")
            else:
                console.log(line, level=level, step=f"opensfm/{modality}")

        try:
            runner.run(modality, on_log=on_log)
            if modality == "thermal" and ortho_rgb(store.root).is_file():
                mark_step(store, "photogrammetry", StepStatus.DONE, message="OpenSfM complete")
            elif modality == "rgb" and ortho_thermal(store.root).is_file():
                mark_step(store, "photogrammetry", StepStatus.DONE, message="OpenSfM complete")
            _refresh_overlays(store)
            console.end_job(ok=True, message=f"OpenSfM {modality} finished")
        except Exception as e:
            _sfm_logs[modality].append(f"ERROR: {e}")
            console.end_job(ok=False, message=str(e))

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
    layers = []
    for key, path in (
        ("rgb", ortho_rgb(store.root)),
        (
            "thermal",
            ortho_thermal_aligned(store.root)
            if ortho_thermal_aligned(store.root).is_file()
            else ortho_thermal(store.root),
        ),
    ):
        if not path.is_file():
            continue
        info_path = path
        if key == "thermal" and ortho_thermal_aligned(store.root).is_file():
            info_path = ortho_thermal_aligned(store.root)
        meta = inspect_geotiff(info_path)
        b = meta["bounds"]
        wgs = bounds_to_wgs84(b["left"], b["bottom"], b["right"], b["top"], meta.get("crs"))
        # Cache-bust when file changes so MapLibre refetches tiles
        try:
            ver = int(info_path.stat().st_mtime_ns)
        except OSError:
            ver = 0
        maxzoom = estimate_maxzoom(info_path)
        layers.append(
            {
                "id": key,
                "tile_url": f"/api/map/tile/{key}/{{z}}/{{x}}/{{y}}.png?v={ver}",
                "bounds": wgs,
                "crs": "EPSG:4326",
                "native_crs": meta["crs"],
                "maxzoom": maxzoom,
                "tile_size": 256,
                "width": meta["width"],
                "height": meta["height"],
            }
        )
    vectors = {
        "aoi": "/api/detection/geojson/aoi?modality=rgb",
        "grid": "/api/detection/geojson/grid?modality=rgb",
        "panels": "/api/detection/geojson/panels?modality=rgb",
        "aoi_thermal": "/api/detection/geojson/aoi?modality=thermal",
        "grid_thermal": "/api/detection/geojson/grid?modality=thermal",
        "panels_thermal": "/api/detection/geojson/panels?modality=thermal",
        "pairs": "/api/segmentation/pairs.geojson",
    }
    return {"layers": layers, "vectors": vectors}


@app.get("/api/map/tile/{layer_id}/{z}/{x}/{y}.png")
def map_xyz_tile(layer_id: str, z: int, x: int, y: int, v: int | None = None):
    """Full-resolution GeoTIFF XYZ tiles (RGBA, nodata transparent)."""
    _ = v  # cache buster from client
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    root = store.root
    if layer_id == "rgb":
        path = ortho_rgb(root)
    elif layer_id in ("thermal", "thermal_aligned"):
        path = (
            ortho_thermal_aligned(root)
            if ortho_thermal_aligned(root).is_file()
            else ortho_thermal(root)
        )
        if layer_id == "thermal_aligned":
            path = ortho_thermal_aligned(root)
    else:
        raise HTTPException(404, f"Unknown layer {layer_id}")
    if not path.is_file():
        raise HTTPException(404, "Layer not found")
    try:
        png = render_geotiff_xyz_tile(path, z, x, y, size=256)
    except Exception as e:
        raise HTTPException(500, f"Tile render failed: {e}") from e
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=120"},
    )


@app.get("/api/map/overlay/{name}")
def map_overlay(name: str):
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = store.root / "work" / "overlays" / name
    if not path.is_file():
        raise HTTPException(404, "Overlay not found")
    return FileResponse(path, media_type="image/png")


def _layer_geotiff(store, layer_id: str) -> Path:
    root = store.root
    mapping = {
        "rgb": ortho_rgb(root),
        "thermal": ortho_thermal(root),
        "thermal_aligned": ortho_thermal_aligned(root),
    }
    if layer_id == "thermal" and ortho_thermal_aligned(root).is_file():
        # Prefer aligned when present for display, but alignment picking uses raw thermal
        pass
    path = mapping.get(layer_id)
    if path is None or not path.is_file():
        raise HTTPException(404, f"Layer not found: {layer_id}")
    return path


@app.get("/api/ortho/{layer_id}/meta")
def ortho_meta(layer_id: str) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    # For alignment, thermal means the unaligned thermal ortho
    if layer_id == "thermal":
        path = ortho_thermal(store.root)
    elif layer_id == "thermal_aligned":
        path = ortho_thermal_aligned(store.root)
    elif layer_id == "rgb":
        path = ortho_rgb(store.root)
    else:
        raise HTTPException(400, "layer_id must be rgb, thermal, or thermal_aligned")
    if not path.is_file():
        raise HTTPException(404, f"Ortho not found: {layer_id}")
    return inspect_geotiff(path)


@app.get("/api/ortho/{layer_id}/window")
def ortho_window(
    layer_id: str,
    col_off: int = Query(..., ge=0),
    row_off: int = Query(..., ge=0),
    width: int = Query(..., ge=1),
    height: int = Query(..., ge=1),
    out_w: int = Query(512, ge=1, le=4096),
    out_h: int = Query(512, ge=1, le=4096),
):
    """Serve a sharp PNG window read from the full GeoTIFF (not the low-res preview)."""
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    if layer_id == "thermal":
        path = ortho_thermal(store.root)
    elif layer_id == "thermal_aligned":
        path = ortho_thermal_aligned(store.root)
    elif layer_id == "rgb":
        path = ortho_rgb(store.root)
    else:
        raise HTTPException(400, "Invalid layer_id")
    if not path.is_file():
        raise HTTPException(404, f"Ortho not found: {layer_id}")
    try:
        png = render_geotiff_window(path, col_off, row_off, width, height, out_w, out_h)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/ortho/{layer_id}/geo-window")
def ortho_geo_window(
    layer_id: str,
    west: float = Query(...),
    south: float = Query(...),
    east: float = Query(...),
    north: float = Query(...),
    out_w: int = Query(512, ge=1, le=4096),
    out_h: int = Query(512, ge=1, le=4096),
):
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    if layer_id == "thermal":
        path = ortho_thermal(store.root)
    elif layer_id == "thermal_aligned":
        path = ortho_thermal_aligned(store.root)
    elif layer_id == "rgb":
        path = ortho_rgb(store.root)
    else:
        raise HTTPException(400, "Invalid layer_id")
    if not path.is_file():
        raise HTTPException(404, f"Ortho not found: {layer_id}")
    try:
        png = render_geotiff_geo_window(path, west, south, east, north, out_w, out_h)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/ortho/{layer_id}/match-rgb-window")
def ortho_match_rgb_window(
    layer_id: str,
    col_off: int = Query(..., ge=0),
    row_off: int = Query(..., ge=0),
    width: int = Query(..., ge=1),
    height: int = Query(..., ge=1),
    out_w: int = Query(512, ge=1, le=4096),
    out_h: int = Query(512, ge=1, le=4096),
):
    """
    Reproject layer onto the exact RGB pixel-window grid (for overlay preview).
    Must use the same col/row/width/height/out size as the RGB /window request.
    """
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    if layer_id == "thermal":
        path = ortho_thermal(store.root)
    elif layer_id == "thermal_aligned":
        path = ortho_thermal_aligned(store.root)
    else:
        raise HTTPException(400, "layer_id must be thermal or thermal_aligned")
    rgb = ortho_rgb(store.root)
    if not path.is_file():
        raise HTTPException(404, f"Ortho not found: {layer_id}")
    if not rgb.is_file():
        raise HTTPException(404, "RGB ortho required as reference grid")
    try:
        png = render_geotiff_matched_to_ref_window(
            path, rgb, col_off, row_off, width, height, out_w, out_h
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/alignment/status")
def alignment_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    wf = store.read_workflow()
    gcps_path = store.root / "alignment" / "gcps.json"
    gcps = None
    if gcps_path.is_file():
        import json

        gcps = json.loads(gcps_path.read_text(encoding="utf-8"))
    aligned = ortho_thermal_aligned(store.root)
    mtime_ns = int(aligned.stat().st_mtime_ns) if aligned.is_file() else None
    return {
        "status": wf.alignment.status.value,
        "message": wf.alignment.message,
        "has_aligned": aligned.is_file(),
        "aligned_mtime_ns": mtime_ns,
        "gcps": gcps,
    }


@app.post("/api/alignment/preview")
def alignment_preview(body: AlignBody) -> dict[str, Any]:
    """Write aligned thermal + GCPs without marking the workflow step done."""
    store = get_store()
    console = get_console()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    rgb = ortho_rgb(store.root)
    thermal = ortho_thermal(store.root)
    if not rgb.is_file() or not thermal.is_file():
        raise HTTPException(400, "RGB and thermal orthophotos required")
    out = ortho_thermal_aligned(store.root)
    console.begin_job("Ortho alignment preview", detail="Estimating affine transform")
    store.checkpoint("Before ortho alignment preview")
    try:
        console.set_progress(15, detail="Reading control points", step="gcps")
        console.set_progress(35, detail="Estimating 4-point affine", step="affine")
        result = apply_georef_rewrite(
            rgb, thermal, out, body.target_points, body.ref_points
        )
        console.set_progress(70, detail="Writing thermal_aligned.tif (metadata rewrite)", step="write")
        save_alignment_artifacts(store.root, body.ref_points, body.target_points, result)
        console.set_progress(88, detail="Refreshing map overlays", step="overlays")
        _refresh_overlays(store)
        store.autosave()
        console.end_job(ok=True, message="Alignment preview ready — check the overlay")
    except Exception as e:
        console.end_job(ok=False, message=str(e))
        raise HTTPException(400, str(e)) from e
    mtime_ns = int(out.stat().st_mtime_ns) if out.is_file() else None
    return {
        "result": result,
        "preview": True,
        "has_aligned": True,
        "aligned_mtime_ns": mtime_ns,
        **_project_payload(store),
    }


@app.post("/api/alignment/confirm")
def alignment_confirm() -> dict[str, Any]:
    store = get_store()
    console = get_console()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    if not ortho_thermal_aligned(store.root).is_file():
        raise HTTPException(400, "No aligned thermal orthophoto to confirm")
    console.begin_job("Save alignment", detail="Updating workflow")
    try:
        mark_step(store, "alignment", StepStatus.DONE, message="Thermal georef aligned to RGB")
        store.autosave()
        console.end_job(ok=True, message="Alignment saved")
    except Exception as e:
        console.end_job(ok=False, message=str(e))
        raise
    return _project_payload(store)


@app.post("/api/alignment/apply")
def alignment_apply(body: AlignBody) -> dict[str, Any]:
    """Legacy one-shot apply (preview + confirm). Prefer preview/confirm."""
    alignment_preview(body)
    return alignment_confirm()


@app.get("/api/detection/status")
def api_detection_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    st = detection_status(store.root)
    st["job"] = detection_job_status()
    return st


@app.get("/api/detection/aoi")
def api_detection_get_aoi(modality: Literal["rgb", "thermal"] = "rgb") -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    fc = load_geojson(store.root, "aoi", modality=modality)
    if not fc:
        raise HTTPException(404, "No AOI saved")
    return fc


@app.put("/api/detection/aoi")
def api_detection_put_aoi(body: AoiBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    store.checkpoint("Before AOI update")
    try:
        path = save_aoi_geojson(
            store.root,
            body.ring,
            modality=body.modality,
            regenerate_grid=body.regenerate_grid,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    store.autosave()
    return {
        "ok": True,
        "path": str(path),
        "modality": body.modality,
        "geojson": load_geojson(store.root, "aoi", modality=body.modality),
        "grid": load_geojson(store.root, "grid", modality=body.modality)
        if body.regenerate_grid
        else None,
    }


@app.post("/api/detection/grid")
def api_detection_grid(body: GridBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    store.checkpoint("Before grid generate")
    try:
        result = generate_grid(
            store.root, rows=body.rows, cols=body.cols, modality=body.modality
        )
    except FileNotFoundError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    store.autosave()
    result["geojson"] = load_geojson(store.root, "grid", modality=body.modality)
    return result


@app.post("/api/detection/grid/copy-to-thermal")
def api_detection_copy_to_thermal() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    store.checkpoint("Before copy RGB grid to thermal")
    try:
        result = copy_rgb_grid_to_thermal(store.root)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e)) from e
    store.autosave()
    result["thermal_aoi"] = load_geojson(store.root, "aoi", modality="thermal")
    result["thermal_grid"] = load_geojson(store.root, "grid", modality="thermal")
    return result


@app.post("/api/detection/run")
def api_detection_run(body: DetectRunBody) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    try:
        start_detection_job(
            store,
            modality="both",
            confidence=body.confidence,
            nms_iou=body.nms_iou,
            num_templates=body.num_templates,
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"started": True, "job": detection_job_status()}


@app.get("/api/detection/job")
def api_detection_job() -> dict[str, Any]:
    return detection_job_status()


@app.get("/api/detection/geojson/{name}")
def api_detection_geojson(
    name: str,
    modality: Literal["rgb", "thermal"] = "rgb",
) -> dict[str, Any]:
    if name not in ("aoi", "grid", "panels"):
        raise HTTPException(404, "Unknown layer")
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    fc = load_geojson(store.root, name, modality=modality)
    if not fc:
        return {"type": "FeatureCollection", "features": []}
    return fc


@app.delete("/api/detection/panel/{panel_id}")
def api_detection_delete_panel(
    panel_id: str,
    modality: Literal["rgb", "thermal"] = "rgb",
) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    import json

    path = detection_dir(store.root, modality) / "panels.geojson"
    fc = load_geojson(store.root, "panels", modality=modality)
    if not fc:
        raise HTTPException(404, "No panels")
    store.checkpoint("Before panel delete")
    feats = [
        f
        for f in (fc.get("features") or [])
        if str((f.get("properties") or {}).get("id") or f.get("id") or "") != panel_id
    ]
    fc["features"] = feats
    path.write_text(json.dumps(fc), encoding="utf-8")
    store.autosave()
    return {"ok": True, "panel_count": len(feats), "modality": modality}


@app.post("/api/detection/clear")
def api_detection_clear(
    modality: Literal["rgb", "thermal"] | None = None,
) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    store.checkpoint("Before clear detection")
    clear_detection(store.root, modality=modality)
    store.autosave()
    return detection_status(store.root)


@app.get("/api/segmentation/status")
def api_segmentation_status() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    st = segmentation_status(store.root)
    st["job"] = segmentation_job_status()
    return st


@app.post("/api/segmentation/run")
def api_segmentation_run(body: SegmentRunBody = SegmentRunBody()) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    margin = body.margin_factor
    try:
        start_segmentation_job(store, margin_factor=margin)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"started": True, "job": segmentation_job_status()}


@app.get("/api/segmentation/job")
def api_segmentation_job() -> dict[str, Any]:
    return segmentation_job_status()


@app.get("/api/segmentation/pairs")
def api_segmentation_pairs() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = segmentation_root(store.root) / "pairs.json"
    if not path.is_file():
        return {"pairs": [], "count": 0}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/segmentation/pairs.geojson")
def api_segmentation_pairs_geojson() -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    path = segmentation_root(store.root) / "pairs.geojson"
    if not path.is_file():
        return {"type": "FeatureCollection", "features": []}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/segmentation/panel/{panel_id}/preview/{kind}")
def api_segmentation_panel_preview(panel_id: str, kind: str):
    if kind not in ("rgb", "thermal"):
        raise HTTPException(404, "kind must be rgb or thermal")
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    safe = "".join(c for c in panel_id if c.isalnum() or c in "-_")
    path = segmentation_root(store.root) / "panels" / safe / f"{kind}.png"
    if not path.is_file():
        raise HTTPException(404, "Preview not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/segmentation/panel/{panel_id}/meta")
def api_segmentation_panel_meta(panel_id: str) -> dict[str, Any]:
    store = get_store()
    if not store.is_open:
        raise HTTPException(404, "No project open")
    safe = "".join(c for c in panel_id if c.isalnum() or c in "-_")
    path = segmentation_root(store.root) / "panels" / safe / "meta.json"
    if not path.is_file():
        raise HTTPException(404, "Meta not found")
    import json

    return json.loads(path.read_text(encoding="utf-8"))


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

    @app.get("/")
    def spa_index():
        """No-cache index so redeploys aren't stuck on an old hashed JS bundle."""
        index = _STATIC / "index.html"
        return FileResponse(
            index,
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )

    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
