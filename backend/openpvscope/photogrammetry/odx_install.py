"""Download and silently install native ODX (WebODM engine) for photogrammetry."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openpvscope.photogrammetry.odx import find_odx_root, probe_odx

DEFAULT_ODX_DIR = Path(r"C:\ODX")
GITHUB_API_LATEST = "https://api.github.com/repos/WebODM/ODX/releases/latest"
_USER_AGENT = "OpenPVScope-odx-install"


@dataclass
class OdxInstallState:
    status: str = "idle"  # idle | running | done | error
    message: str = ""
    error: str | None = None
    progress: float | None = None  # 0..1 during download; None = indeterminate
    odx: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "progress": self.progress,
            "odx": self.odx or probe_odx(),
        }


_lock = threading.Lock()
_state = OdxInstallState(odx=probe_odx())
_thread: threading.Thread | None = None


def get_install_state() -> dict[str, Any]:
    with _lock:
        if _state.status != "running":
            _state.odx = probe_odx()
        return _state.to_public()


def _set(**kwargs: Any) -> None:
    with _lock:
        for k, v in kwargs.items():
            setattr(_state, k, v)


def resolve_odx_setup_url(version: str | None = None) -> tuple[str, str]:
    """Return (download_url, filename) for ODX_Setup_*.exe."""
    if version:
        tag = version if version.startswith("v") else version
        api = f"https://api.github.com/repos/WebODM/ODX/releases/tags/{tag}"
        # try without forcing v prefix variants
        try:
            rel = _github_json(api)
        except urllib.error.HTTPError:
            alt = version.lstrip("v")
            rel = _github_json(f"https://api.github.com/repos/WebODM/ODX/releases/tags/{alt}")
    else:
        rel = _github_json(GITHUB_API_LATEST)

    assets = rel.get("assets") or []
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.startswith("ODX_Setup_") and name.endswith(".exe"):
            url = asset.get("browser_download_url")
            if url:
                return str(url), name
    raise FileNotFoundError(
        f"No ODX_Setup_*.exe asset on release {rel.get('tag_name', '?')}"
    )


def _github_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_odx_setup(
    dest: Path,
    *,
    version: str | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> Path:
    url, name = resolve_odx_setup_url(version)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = dest if dest.suffix.lower() == ".exe" else dest / name

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        chunk = 1024 * 256
        with open(out, "wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                if on_progress and total > 0:
                    on_progress(min(1.0, downloaded / total), f"Downloading {name}…")
    if on_progress:
        on_progress(1.0, f"Downloaded {name}")
    return out


def run_silent_odx_setup(setup_exe: Path, install_dir: Path = DEFAULT_ODX_DIR) -> None:
    setup_exe = Path(setup_exe)
    install_dir = Path(install_dir)
    if not setup_exe.is_file():
        raise FileNotFoundError(f"ODX setup not found: {setup_exe}")
    if os.name != "nt":
        raise RuntimeError("In-app ODX install is only supported on Windows.")

    args = [
        str(setup_exe),
        "/VERYSILENT",
        "/NORESTART",
        "/SUPPRESSMSGBOXES",
        f"/DIR={install_dir}",
    ]
    proc = subprocess.run(args, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ODX setup exited with code {proc.returncode}. "
            "Try installing manually from https://github.com/WebODM/ODX/releases"
        )
    if not (install_dir / "run.bat").is_file() and find_odx_root() is None:
        raise RuntimeError(
            f"ODX setup finished but {install_dir / 'run.bat'} was not found. "
            "Install from https://github.com/WebODM/ODX/releases"
        )


def _install_worker(install_dir: Path, version: str | None) -> None:
    tmp_dir: Path | None = None
    try:
        probe = probe_odx()
        if probe.get("available"):
            _set(status="done", message="ODX already installed", error=None, progress=1.0, odx=probe)
            return

        if os.name != "nt":
            raise RuntimeError("In-app ODX install is only supported on Windows.")

        _set(status="running", message="Resolving latest ODX release…", error=None, progress=None)
        tmp_dir = Path(tempfile.mkdtemp(prefix="openpvscope-odx-"))

        def on_progress(frac: float, msg: str) -> None:
            _set(status="running", message=msg, progress=frac * 0.85, error=None)

        setup = download_odx_setup(tmp_dir, version=version, on_progress=on_progress)
        _set(
            status="running",
            message=f"Installing ODX to {install_dir}…",
            progress=0.9,
            error=None,
        )
        run_silent_odx_setup(setup, install_dir)
        probe = probe_odx()
        if not probe.get("available"):
            raise RuntimeError("Install finished but ODX was not detected. Set OPENPVSCOPE_ODX_ROOT if needed.")
        _set(status="done", message="ODX installed successfully", error=None, progress=1.0, odx=probe)
    except Exception as e:
        _set(
            status="error",
            message="ODX install failed",
            error=str(e),
            progress=None,
            odx=probe_odx(),
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def start_odx_install(
    *,
    install_dir: Path | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Start background install. Returns current public state. Raises RuntimeError if busy."""
    global _thread
    install_dir = Path(install_dir) if install_dir else DEFAULT_ODX_DIR

    with _lock:
        if _state.status == "running":
            raise RuntimeError("ODX install already in progress")
        probe = probe_odx()
        if probe.get("available"):
            _state.status = "done"
            _state.message = "ODX already installed"
            _state.error = None
            _state.progress = 1.0
            _state.odx = probe
            return _state.to_public()

        _state.status = "running"
        _state.message = "Starting ODX install…"
        _state.error = None
        _state.progress = None
        _state.odx = probe
        _thread = threading.Thread(
            target=_install_worker,
            args=(install_dir, version),
            name="odx-install",
            daemon=True,
        )
        _thread.start()
        return _state.to_public()
