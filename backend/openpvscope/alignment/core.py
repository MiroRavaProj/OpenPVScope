"""AKAZE affine + metre-tile TPS thermal→RGB alignment."""

from __future__ import annotations

import gc
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from openpvscope.alignment.register_thermal import (
    GLOBAL_MAX_M,
    LOCAL_MAX_M,
    MAX_REG_GSD_M,
    NODATA,
    STRIDE_M,
    TILE_M,
    register_thermal_to_rgb,
)

DEFAULT_PARAMS: dict[str, float] = {
    "max_reg_gsd_m": float(MAX_REG_GSD_M),
    "global_max_m": float(GLOBAL_MAX_M),
    "local_max_m": float(LOCAL_MAX_M),
    "tile_m": float(TILE_M),
    "stride_m": float(STRIDE_M),
    "nodata": float(NODATA),
}

_proc_lock = threading.Lock()
_align_proc: subprocess.Popen[str] | None = None
_cancel_requested = False


class AlignmentCancelled(RuntimeError):
    """Raised when alignment subprocess is killed by user cancel."""


def _kill_process_tree(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    pid = proc.pid
    if sys.platform == "win32" and pid:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return
        except Exception:
            pass
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except OSError:
            pass


def request_cancel_alignment() -> bool:
    """Kill the in-flight alignment worker if any. Returns True if a cancel was requested."""
    global _cancel_requested, _align_proc
    with _proc_lock:
        proc = _align_proc
        if proc is None or proc.poll() is not None:
            return False
        _cancel_requested = True
        _kill_process_tree(proc)
        return True


def _run_alignment_subprocess(
    reference_path: Path,
    target_path: Path,
    output_path: Path,
    *,
    max_reg_gsd_m: float,
    global_max_m: float,
    local_max_m: float,
    tile_m: float,
    stride_m: float,
    nodata: float,
) -> dict[str, Any]:
    """Run registration in a child process so peak RAM is released on exit."""
    global _align_proc, _cancel_requested
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("register_thermal.py").resolve()
    with tempfile.TemporaryDirectory(prefix="openpvscope_align_") as tmp:
        meta_path = Path(tmp) / "meta.json"
        cmd = [
            sys.executable,
            str(script),
            str(reference_path),
            str(target_path),
            "-o",
            str(output_path),
            "--nodata",
            str(nodata),
            "--max-reg-gsd-m",
            str(max_reg_gsd_m),
            "--global-max-m",
            str(global_max_m),
            "--local-max-m",
            str(local_max_m),
            "--tile-m",
            str(tile_m),
            "--stride-m",
            str(stride_m),
            "--meta-json",
            str(meta_path),
        ]
        gc.collect()
        with _proc_lock:
            _cancel_requested = False
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            _align_proc = proc
        try:
            stdout, stderr = proc.communicate()
            code = proc.returncode
        finally:
            with _proc_lock:
                cancelled = _cancel_requested
                _align_proc = None
                _cancel_requested = False
        if cancelled:
            raise AlignmentCancelled("Alignment cancelled")
        if code != 0:
            err = (stderr or stdout or "").strip()
            raise RuntimeError(err or f"Alignment subprocess failed (exit {code})")
        if not meta_path.is_file():
            raise RuntimeError("Alignment subprocess finished but wrote no metadata")
        return json.loads(meta_path.read_text(encoding="utf-8"))


def run_alignment(
    reference_path: Path,
    target_path: Path,
    output_path: Path,
    *,
    max_reg_gsd_m: float = float(MAX_REG_GSD_M),
    global_max_m: float = float(GLOBAL_MAX_M),
    local_max_m: float = float(LOCAL_MAX_M),
    tile_m: float = float(TILE_M),
    stride_m: float = float(STRIDE_M),
    nodata: float = float(NODATA),
    isolate: bool = True,
) -> dict[str, Any]:
    """Register thermal to RGB via AKAZE/affine Pass 1 + metre-tile TPS Pass 2."""
    kwargs = dict(
        nodata=nodata,
        max_reg_gsd_m=max_reg_gsd_m,
        global_max_m=global_max_m,
        local_max_m=local_max_m,
        tile_m=tile_m,
        stride_m=stride_m,
    )
    try:
        if isolate:
            return _run_alignment_subprocess(
                reference_path, target_path, output_path, **kwargs
            )
        return register_thermal_to_rgb(reference_path, target_path, output_path, **kwargs)
    finally:
        gc.collect()


def save_alignment_artifacts(
    project_root: Path,
    params: dict[str, Any],
    meta: dict[str, Any],
) -> None:
    align_dir = Path(project_root) / "alignment"
    align_dir.mkdir(parents=True, exist_ok=True)
    (align_dir / "params.json").write_text(
        json.dumps(params, indent=2),
        encoding="utf-8",
    )
    (align_dir / "transform.json").write_text(
        json.dumps(
            {
                "method": meta.get("method", "akaze_tps"),
                "output": meta.get("output_path"),
                "pass1_output": meta.get("pass1_output_path"),
                "output_grid": meta.get("output_grid", "native_thermal"),
                "pass1": meta.get("pass1"),
                "gsd_work_m": meta.get("gsd_work_m"),
                "gsd_thermal_m": meta.get("gsd_thermal_m"),
                "max_reg_gsd_m": meta.get("max_reg_gsd_m"),
                "work_shape_hw": meta.get("work_shape_hw"),
                "native_shape_hw": meta.get("native_shape_hw"),
                "affine_2x3_work": meta.get("affine_2x3_work"),
                "n_tile_pairs": meta.get("n_tile_pairs"),
                "n_akaze_matches": meta.get("n_akaze_matches"),
                "n_matches": meta.get("n_matches"),
                "n_inliers": meta.get("n_inliers"),
                "scale_x": meta.get("scale_x"),
                "scale_y": meta.get("scale_y"),
                "rotation_deg": meta.get("rotation_deg"),
                "tx": meta.get("tx"),
                "ty": meta.get("ty"),
                "ecc_cc": meta.get("ecc_cc"),
                "ctrl_pts": meta.get("ctrl_pts"),
                "tile_px": meta.get("tile_px"),
                "stride_px": meta.get("stride_px"),
                "prep_reproject_s": meta.get("prep_reproject_s"),
                "global_pass1_s": meta.get("global_pass1_s"),
                "tile_tps_s": meta.get("tile_tps_s"),
                "runtime_s": meta.get("runtime_s"),
                "params": params,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
