"""Create / open / save .opsx project packages."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from openpvscope.domain.models import Manifest, Workflow, default_workflow
from openpvscope.project.paths import ensure_project_tree

# Extensions stored without compression (already large/compressed)
_STORE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".laz", ".las", ".ply", ".joblib"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ProjectStore:
    """Manages a working directory that can be packed/unpacked as .opsx."""

    def __init__(self, cache_root: Path | None = None) -> None:
        self.cache_root = Path(cache_root or Path.home() / ".openpvscope" / "cache")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._active: Path | None = None
        self._opsx_path: Path | None = None

    @property
    def root(self) -> Path:
        if self._active is None:
            raise RuntimeError("No project is open")
        return self._active

    @property
    def opsx_path(self) -> Path | None:
        return self._opsx_path

    @property
    def is_open(self) -> bool:
        return self._active is not None

    def create(self, name: str, opsx_path: Path | None = None) -> Path:
        project_id = uuid.uuid4().hex[:12]
        work = self.cache_root / project_id
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        ensure_project_tree(work)

        now = _utc_now()
        manifest = Manifest(
            name=name,
            created_at=now,
            updated_at=now,
            id=project_id,
        )
        workflow = default_workflow()
        _write_json(work / "manifest.json", manifest.model_dump(mode="json"))
        _write_json(work / "workflow.json", workflow.model_dump(mode="json"))

        self._active = work
        self._opsx_path = Path(opsx_path) if opsx_path else None
        if self._opsx_path:
            self.save()
        return work

    def open_opsx(self, opsx_path: Path) -> Path:
        opsx_path = Path(opsx_path)
        if not opsx_path.is_file():
            raise FileNotFoundError(f"Project not found: {opsx_path}")

        # Peek manifest for id
        with zipfile.ZipFile(opsx_path, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
        project_id = manifest.get("id") or uuid.uuid4().hex[:12]
        work = self.cache_root / project_id
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)

        with zipfile.ZipFile(opsx_path, "r") as zf:
            zf.extractall(work)

        ensure_project_tree(work)
        self._active = work
        self._opsx_path = opsx_path
        return work

    def open_directory(self, directory: Path) -> Path:
        directory = Path(directory)
        if not (directory / "manifest.json").is_file():
            raise ValueError(f"Not an OpenPVScope working directory: {directory}")
        ensure_project_tree(directory)
        self._active = directory
        self._opsx_path = None
        return directory

    def save(self, opsx_path: Path | None = None) -> Path:
        if self._active is None:
            raise RuntimeError("No project is open")
        target = Path(opsx_path or self._opsx_path or (self._active / "project.opsx"))
        target.parent.mkdir(parents=True, exist_ok=True)

        # Touch updated_at
        manifest_path = self._active / "manifest.json"
        if manifest_path.is_file():
            data = _read_json(manifest_path)
            data["updated_at"] = _utc_now()
            _write_json(manifest_path, data)

        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".opsx")
        os.close(tmp_fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w") as zf:
                for path in sorted(self._active.rglob("*")):
                    if path.is_dir():
                        continue
                    rel = path.relative_to(self._active).as_posix()
                    compress = (
                        zipfile.ZIP_STORED
                        if path.suffix.lower() in _STORE_SUFFIXES
                        else zipfile.ZIP_DEFLATED
                    )
                    zf.write(path, rel, compress_type=compress)
            if target.exists():
                target.unlink()
            shutil.move(str(tmp), str(target))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

        self._opsx_path = target
        return target

    def close(self) -> None:
        self._active = None
        self._opsx_path = None

    def read_manifest(self) -> Manifest:
        return Manifest.model_validate(_read_json(self.root / "manifest.json"))

    def read_workflow(self) -> Workflow:
        return Workflow.model_validate(_read_json(self.root / "workflow.json"))

    def write_workflow(self, workflow: Workflow) -> None:
        _write_json(self.root / "workflow.json", workflow.model_dump(mode="json"))

    def update_manifest_name(self, name: str) -> Manifest:
        m = self.read_manifest()
        data = m.model_dump(mode="json")
        data["name"] = name
        data["updated_at"] = _utc_now()
        _write_json(self.root / "manifest.json", data)
        return Manifest.model_validate(data)


# Process-wide store for the local desktop/API process
_store: ProjectStore | None = None


def get_store() -> ProjectStore:
    global _store
    if _store is None:
        _store = ProjectStore()
    return _store
