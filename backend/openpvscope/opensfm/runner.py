"""OpenSfM 1.0 runner — sparse + dense → georeferenced ortho.tif."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

OPENSFM_COMMANDS: tuple[str, ...] = (
    "extract_metadata",
    "detect_features",
    "match_features",
    "create_tracks",
    "reconstruct",
    "undistort",
    "dense_clustering",
    "compute_depthmaps",
    "fuse_depthmaps",
)

LogCallback = Callable[[str], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_opensfm_root() -> Path | None:
    env = os.environ.get("OPENPVSCOPE_OPENSFM_ROOT")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    # Relative to package / repo: engines/opensfm
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "engines" / "opensfm",  # OpenPVScope/engines/opensfm
        Path.cwd() / "engines" / "opensfm",
    ]
    for c in candidates:
        if c.is_dir():
            return c

    # PATH
    for name in ("opensfm.bat", "opensfm"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve().parent.parent
    return None


def opensfm_executable(root: Path | None = None) -> Path:
    root = root or find_opensfm_root()
    if root is None:
        raise FileNotFoundError(
            "OpenSfM not found. Set OPENPVSCOPE_OPENSFM_ROOT or install under engines/opensfm/."
        )
    bat = root / "bin" / "opensfm.bat"
    sh = root / "bin" / "opensfm"
    if bat.is_file():
        return bat
    if sh.is_file():
        return sh
    which = shutil.which("opensfm.bat") or shutil.which("opensfm")
    if which:
        return Path(which)
    raise FileNotFoundError(f"No opensfm executable under {root}/bin")


def probe_opencl() -> dict:
    """Best-effort OpenCL availability check."""
    try:
        import pyopencl as cl  # type: ignore

        platforms = cl.get_platforms()
        devices = []
        for p in platforms:
            for d in p.get_devices():
                devices.append({"platform": p.name, "device": d.name, "type": str(d.type)})
        return {"available": bool(devices), "devices": devices}
    except Exception as e:
        # Try clinfo
        clinfo = shutil.which("clinfo")
        if clinfo:
            try:
                r = subprocess.run([clinfo, "-l"], capture_output=True, text=True, timeout=10)
                ok = r.returncode == 0 and bool(r.stdout.strip())
                return {"available": ok, "devices": [], "clinfo": r.stdout[:2000]}
            except Exception:
                pass
        return {"available": False, "devices": [], "error": str(e)}


@dataclass
class JobState:
    modality: str
    status: str = "pending"
    current_command: str | None = None
    log: list[str] = field(default_factory=list)
    error: str | None = None
    ortho_path: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class OpenSfMRunner:
    def __init__(self, project_root: Path, opensfm_root: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.opensfm_root = opensfm_root or find_opensfm_root()
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def get_job(self, modality: str) -> JobState | None:
        return self._jobs.get(modality)

    def prepare_dataset(self, modality: str, image_files: list[Path]) -> Path:
        """Create OpenSfM dataset folder and copy images."""
        assert modality in ("rgb", "thermal")
        ds = self.project_root / "photogrammetry" / modality
        images = ds / "images"
        if ds.exists():
            shutil.rmtree(ds)
        images.mkdir(parents=True)

        if modality == "thermal":
            from openpvscope.thermal.dji import prepare_thermal_for_opensfm

            for src in image_files:
                prepare_thermal_for_opensfm(src, images)
        else:
            for src in image_files:
                src = Path(src)
                dest = images / src.name
                shutil.copy2(src, dest)

        job_meta = {
            "modality": modality,
            "image_count": len(list(images.iterdir())),
            "prepared_at": _utc_now(),
        }
        (self.project_root / "photogrammetry" / f"{modality}_job.json").write_text(
            json.dumps(job_meta, indent=2), encoding="utf-8"
        )
        return ds

    def run(
        self,
        modality: str,
        on_log: LogCallback | None = None,
        skip_opencl_check: bool = False,
    ) -> Path:
        """Run full OpenSfM pipeline; return path to ortho.tif."""
        ds = self.project_root / "photogrammetry" / modality
        if not (ds / "images").is_dir():
            raise FileNotFoundError(f"Dataset not prepared: {ds}")

        if not skip_opencl_check:
            cl = probe_opencl()
            if not cl.get("available"):
                raise RuntimeError(
                    "OpenCL GPU not detected. OpenSfM dense orthophoto requires OpenCL. "
                    "Update GPU drivers, or skip photogrammetry and import GeoTIFFs."
                )

        exe = opensfm_executable(self.opensfm_root)
        job = JobState(modality=modality, status="running", started_at=_utc_now())
        with self._lock:
            self._jobs[modality] = job

        def log(line: str) -> None:
            job.log.append(line)
            if on_log:
                on_log(line)

        try:
            for cmd in OPENSFM_COMMANDS:
                job.current_command = cmd
                log(f">>> opensfm {cmd}")
                self._run_cmd(exe, cmd, ds, log)

            job.current_command = "dense_merging --georeferenced"
            log(">>> opensfm dense_merging --georeferenced")
            self._run_cmd(exe, "dense_merging", ds, log, extra_args=["--georeferenced"])

            ortho_src = ds / "undistorted" / "depthmaps" / "ortho.tif"
            if not ortho_src.is_file():
                raise FileNotFoundError(f"Expected orthophoto missing: {ortho_src}")

            ortho_dest = self.project_root / "inputs" / "ortho" / f"{modality}.tif"
            ortho_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ortho_src, ortho_dest)

            job.ortho_path = str(ortho_dest)
            job.status = "done"
            job.finished_at = _utc_now()
            job.current_command = None

            meta_path = self.project_root / "photogrammetry" / f"{modality}_job.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            meta.update(
                {
                    "status": "done",
                    "ortho": str(ortho_dest),
                    "finished_at": job.finished_at,
                }
            )
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return ortho_dest
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.finished_at = _utc_now()
            raise

    def _run_cmd(
        self,
        exe: Path,
        command: str,
        dataset: Path,
        log: LogCallback,
        extra_args: list[str] | None = None,
    ) -> None:
        args = [str(exe), command, str(dataset)]
        if extra_args:
            # dense_merging --georeferenced DATA  → insert flags before path
            args = [str(exe), command, *extra_args, str(dataset)]
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(self.opensfm_root) if self.opensfm_root else None,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log(line.rstrip())
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"OpenSfM command failed ({command}) with exit code {code}")
