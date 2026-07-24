"""Native WebODM ODX runner — images/ → odm_orthophoto.tif."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    import winreg
except ImportError:  # non-Windows
    winreg = None  # type: ignore[assignment]

# Coarse ODX pipeline stages (progress UI). Matched against log lines.
ODX_STAGES: tuple[str, ...] = (
    "dataset",
    "split",
    "merge",
    "opensfm",
    "openmvs",
    "odm_filterpoints",
    "odm_meshing",
    "mvs_texturing",
    "odm_georeferencing",
    "odm_dem",
    "odm_orthophoto",
    "odm_report",
    "odm_postprocess",
)

STAGE_TOTAL: int = len(ODX_STAGES)

# Default CLI flags: orthophoto-focused (skip 3D extras when possible).
DEFAULT_ODX_ARGS: tuple[str, ...] = (
    "--orthophoto-resolution",
    "2",
)

DEFAULT_PRODUCTS: dict[str, bool] = {
    "ortho": True,
    "dense_pc": False,
    "sparse_pc": False,
    "dsm": False,
    "dtm": False,
}

LogCallback = Callable[[str], None]

_STAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE), name) for name in ODX_STAGES
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_odx_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (path / "run.bat").is_file() or (path / "run.py").is_file() or (path / "winrun.bat").is_file()


def _registry_odx_dirs() -> list[Path]:
    """Inno Setup uninstall keys often point at the ODX install dir."""
    if winreg is None:
        return []
    found: list[Path] = []
    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in roots:
        try:
            with winreg.OpenKey(hive, sub) as key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(key, name) as sk:
                            display, _ = winreg.QueryValueEx(sk, "DisplayName")
                            if not isinstance(display, str) or "ODX" not in display.upper():
                                continue
                            try:
                                loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                            except OSError:
                                continue
                            if isinstance(loc, str) and loc.strip():
                                found.append(Path(loc.strip()))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def find_odx_root() -> Path | None:
    """Locate a native ODX install (Windows Setup → typically C:\\ODX)."""
    env = os.environ.get("OPENPVSCOPE_ODX_ROOT")
    if env:
        p = Path(env)
        if _is_odx_root(p):
            return p.resolve()
        if p.is_dir():
            return p.resolve()

    candidates: list[Path] = [
        Path(r"C:\ODX"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ODX",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ODX",
        Path.home() / "ODX",
    ]
    candidates.extend(_registry_odx_dirs())

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "engines" / "odx")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "engines" / "odx")

    here = Path(__file__).resolve()
    try:
        candidates.append(here.parents[3] / "engines" / "odx")
    except IndexError:
        pass
    candidates.append(Path.cwd() / "engines" / "odx")

    seen: set[str] = set()
    for c in candidates:
        try:
            key = str(c.resolve())
        except OSError:
            key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if _is_odx_root(c):
            return c.resolve()

    for name in ("run.bat", "winrun.bat"):
        found = shutil.which(name)
        if found:
            parent = Path(found).resolve().parent
            if _is_odx_root(parent):
                return parent
    return None


def odx_run_script(root: Path | None = None) -> Path:
    root = root or find_odx_root()
    if root is None:
        raise FileNotFoundError(
            "ODX not found. Install ODX from the Photogrammetry screen, "
            "from https://github.com/WebODM/ODX/releases, or set OPENPVSCOPE_ODX_ROOT."
        )
    for name in ("run.bat", "winrun.bat"):
        p = root / name
        if p.is_file():
            return p
    raise FileNotFoundError(f"No run.bat under {root}")


def probe_odx() -> dict:
    root = find_odx_root()
    if root is None:
        return {
            "available": False,
            "root": None,
            "run_script": None,
            "error": (
                "ODX not found. Install ODX from the Photogrammetry screen, "
                "or from https://github.com/WebODM/ODX/releases"
            ),
        }
    try:
        script = odx_run_script(root)
        return {
            "available": True,
            "root": str(root),
            "run_script": str(script),
            "error": None,
        }
    except FileNotFoundError as e:
        return {
            "available": False,
            "root": str(root),
            "run_script": None,
            "error": str(e),
        }


def _match_stage(line: str) -> str | None:
    for pat, name in _STAGE_PATTERNS:
        if pat.search(line):
            return name
    return None


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Terminate ODX and child processes (run.bat → python)."""
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


@dataclass
class JobState:
    modality: str
    status: str = "pending"
    current_command: str | None = None
    stage_index: int = 0
    stage_total: int = STAGE_TOTAL
    stage_name: str | None = None
    cancel_requested: bool = False
    log: list[str] = field(default_factory=list)
    error: str | None = None
    ortho_path: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    _proc: subprocess.Popen | None = field(default=None, repr=False, compare=False)

    def to_public_dict(self) -> dict:
        return {
            "status": self.status,
            "current_command": self.current_command,
            "stage_index": self.stage_index,
            "stage_total": self.stage_total,
            "stage_name": self.stage_name,
            "error": self.error,
            "ortho_path": self.ortho_path,
            "cancelable": self.status == "running" and not self.cancel_requested,
        }


class ODXRunner:
    """Prepare images/ datasets and run native ODX to produce orthophotos."""

    def __init__(self, project_root: Path, odx_root: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.odx_root = odx_root or find_odx_root()
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def get_job(self, modality: str) -> JobState | None:
        return self._jobs.get(modality)

    def request_cancel(self, modality: str) -> bool:
        with self._lock:
            job = self._jobs.get(modality)
            if job is None or job.status != "running":
                return False
            job.cancel_requested = True
            proc = job._proc
        if proc is not None:
            _kill_process_tree(proc)
        return True

    def prepare_dataset(
        self,
        modality: str,
        image_files: list[Path],
        *,
        emissivity: float = 0.95,
        distance: float = 5.0,
        humidity: float = 50.0,
        reflection: float = 25.0,
        parametric_fallback: bool = False,
    ) -> Path:
        """Create ODX dataset folder with images/ (same layout as OpenSfM)."""
        assert modality in ("rgb", "thermal")
        ds = self.project_root / "photogrammetry" / modality
        images = ds / "images"
        if ds.exists():
            shutil.rmtree(ds)
        images.mkdir(parents=True)

        if modality == "thermal":
            from openpvscope.thermal.dji import prepare_thermal_for_photogrammetry

            for src in image_files:
                prepare_thermal_for_photogrammetry(
                    src,
                    images,
                    emissivity=emissivity,
                    distance=distance,
                    humidity=humidity,
                    reflection=reflection,
                    parametric_fallback=parametric_fallback,
                )
        else:
            for src in image_files:
                src = Path(src)
                dest = images / src.name
                shutil.copy2(src, dest)

        job_meta = {
            "modality": modality,
            "engine": "odx",
            "image_count": len(list(images.iterdir())),
            "prepared_at": _utc_now(),
        }
        (self.project_root / "photogrammetry" / f"{modality}_job.json").write_text(
            json.dumps(job_meta, indent=2), encoding="utf-8"
        )
        return ds

    def _copy_products(
        self,
        ds: Path,
        modality: str,
        products: dict[str, bool],
        log: LogCallback,
    ) -> dict[str, str]:
        """Copy requested ODX outputs into photogrammetry/{modality}/exports/ (+ ortho)."""
        from openpvscope.photogrammetry.setup import _merge_products

        prods = _merge_products(products)
        exports = ds / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        copied: dict[str, str] = {}

        def _try_copy(src: Path, dest_name: str, label: str) -> None:
            if not src.is_file():
                log(f"    product missing ({label}): {src}")
                return
            dest = exports / dest_name
            shutil.copy2(src, dest)
            copied[label] = str(dest)
            log(f"    product copied ({label}): {dest}")

        # Orthophoto → inputs/ortho (required)
        ortho_src = ds / "odm_orthophoto" / "odm_orthophoto.tif"
        if not ortho_src.is_file():
            raise FileNotFoundError(f"Expected orthophoto missing: {ortho_src}")
        ortho_dest = self.project_root / "inputs" / "ortho" / f"{modality}.tif"
        ortho_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ortho_src, ortho_dest)
        copied["ortho"] = str(ortho_dest)
        log(f"    product copied (ortho): {ortho_dest}")

        if prods.get("dense_pc"):
            laz = ds / "odm_georeferencing" / "odm_georeferenced_model.laz"
            las = ds / "odm_georeferencing" / "odm_georeferenced_model.las"
            if laz.is_file():
                _try_copy(laz, "point_cloud.laz", "dense_pc")
            elif las.is_file():
                _try_copy(las, "point_cloud.las", "dense_pc")
            else:
                log(f"    product missing (dense_pc): {laz}")

        if prods.get("sparse_pc"):
            sparse_candidates = [
                ds / "opensfm" / "reconstruction.ply",
                ds / "opensfm" / "undistorted" / "reconstruction.ply",
                ds / "opensfm" / "undistorted" / "openmvs" / "scene_dense_sparse.ply",
            ]
            found = next((p for p in sparse_candidates if p.is_file()), None)
            if found:
                _try_copy(found, "sparse.ply", "sparse_pc")
            else:
                log("    product missing (sparse_pc): opensfm/reconstruction.ply")

        if prods.get("dsm"):
            _try_copy(ds / "odm_dem" / "dsm.tif", "dsm.tif", "dsm")
        if prods.get("dtm"):
            _try_copy(ds / "odm_dem" / "dtm.tif", "dtm.tif", "dtm")

        return copied

    def run(
        self,
        modality: str,
        on_log: LogCallback | None = None,
        extra_args: list[str] | None = None,
        products: dict[str, bool] | None = None,
    ) -> Path:
        """Run ODX on photogrammetry/{modality}; return path to inputs/ortho/{modality}.tif."""
        ds = self.project_root / "photogrammetry" / modality
        if not (ds / "images").is_dir():
            raise FileNotFoundError(f"Dataset not prepared: {ds}")

        if self.odx_root is None or not _is_odx_root(self.odx_root):
            self.odx_root = find_odx_root()
        if self.odx_root is None or not _is_odx_root(self.odx_root):
            raise FileNotFoundError(
                "ODX not found. Install ODX from the Photogrammetry screen, "
                "from https://github.com/WebODM/ODX/releases, or set OPENPVSCOPE_ODX_ROOT."
            )

        run_script = odx_run_script(self.odx_root)
        job = JobState(
            modality=modality,
            status="running",
            started_at=_utc_now(),
            stage_total=STAGE_TOTAL,
            stage_index=0,
            stage_name=ODX_STAGES[0],
            current_command="odx run",
        )
        with self._lock:
            self._jobs[modality] = job

        def log(line: str) -> None:
            job.log.append(line)
            if on_log:
                on_log(line)

        try:
            self._raise_if_cancelled(job)
            if extra_args is not None:
                args_extra = list(extra_args)
            else:
                from openpvscope.photogrammetry.setup import build_odx_argv

                args_extra = build_odx_argv(None, products)
            # ODX expects: run --project-path <parent> <dataset_name> [flags]
            parent = str(ds.parent.resolve())
            name = ds.name
            argv = [
                str(run_script),
                "--project-path",
                parent,
                name,
                *args_extra,
            ]
            log(f">>> odx run {name}")
            log(f"    invoke: {run_script.name} --project-path {parent} {name} {' '.join(args_extra)}")

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(self.odx_root),
                creationflags=creationflags,
            )
            job._proc = proc
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if job.cancel_requested:
                        _kill_process_tree(proc)
                        raise RuntimeError("Cancelled")
                    text = line.rstrip()
                    log(text)
                    matched = _match_stage(text)
                    if matched:
                        try:
                            idx = ODX_STAGES.index(matched) + 1
                        except ValueError:
                            idx = job.stage_index
                        if idx >= job.stage_index:
                            job.stage_index = idx
                            job.stage_name = matched
                            job.current_command = matched
                code = proc.wait()
            finally:
                job._proc = None

            if job.cancel_requested:
                raise RuntimeError("Cancelled")
            if code != 0:
                raise RuntimeError(f"ODX failed with exit code {code}")

            prods = products if products is not None else dict(DEFAULT_PRODUCTS)
            copied = self._copy_products(ds, modality, prods, log)
            ortho_dest = Path(copied["ortho"])

            job.ortho_path = str(ortho_dest)
            job.status = "done"
            job.finished_at = _utc_now()
            job.current_command = None
            job.stage_name = None
            job.stage_index = STAGE_TOTAL

            meta_path = self.project_root / "photogrammetry" / f"{modality}_job.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            meta.update(
                {
                    "status": "done",
                    "engine": "odx",
                    "ortho": str(ortho_dest),
                    "products": copied,
                    "finished_at": job.finished_at,
                }
            )
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return ortho_dest
        except Exception as e:
            cancelled = job.cancel_requested or str(e) == "Cancelled"
            job.status = "cancelled" if cancelled else "error"
            job.error = "Cancelled" if cancelled else str(e)
            job.finished_at = _utc_now()
            if cancelled:
                raise RuntimeError("Cancelled") from e
            raise

    @staticmethod
    def _raise_if_cancelled(job: JobState) -> None:
        if job.cancel_requested:
            raise RuntimeError("Cancelled")
