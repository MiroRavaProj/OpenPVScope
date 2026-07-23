"""Project store: live folder + .opsx JSON descriptor; .opsz = zip export."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from openpvscope.domain.models import OPSX_FORMAT_VERSION, Manifest, Workflow, default_workflow
from openpvscope.io_atomic import atomic_write_json
from openpvscope.project.history import HISTORY_DIR, HistoryStatus, ProjectHistory
from openpvscope.project.paths import STAGE_DIRS, ensure_project_tree
from openpvscope.settings import add_recent_project, load_settings

_STORE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".laz", ".las", ".ply", ".joblib"}
_ALWAYS_EXCLUDE_PREFIXES = (f"{HISTORY_DIR}/",)

OpszMode = Literal["full", "light"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_slug(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    return slug or "project"


def _is_zip_file(path: Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def _should_exclude(rel: str, mode: OpszMode, light_exclude: list[str]) -> bool:
    rel = rel.replace("\\", "/")
    for prefix in _ALWAYS_EXCLUDE_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return True
    if mode == "light":
        for prefix in light_exclude:
            p = prefix.replace("\\", "/")
            if not p.endswith("/"):
                p = p + "/"
            if rel == p.rstrip("/") or rel.startswith(p):
                return True
    return False


class ProjectStore:
    """
    Live project = a directory on disk containing:
      - <name>.opsx   JSON descriptor (paths + workflow snapshot)
      - inputs/, alignment/, ... working data

    .opsz = portable zip export of that whole directory.
    """

    def __init__(self) -> None:
        self._root: Path | None = None
        self._opsx_path: Path | None = None
        self._history: ProjectHistory | None = None

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("No project is open")
        return self._root

    @property
    def opsx_path(self) -> Path | None:
        return self._opsx_path

    @property
    def is_open(self) -> bool:
        return self._root is not None

    def _bind_history(self) -> None:
        self._history = ProjectHistory(self.root) if self._root else None

    def history_status(self) -> HistoryStatus:
        if self._history is None:
            return HistoryStatus(False, False, None, None, 0, 0)
        return self._history.status()

    def checkpoint(self, label: str) -> None:
        if self._history is not None:
            self._history.checkpoint(label)

    def undo(self) -> str | None:
        if self._history is None:
            return None
        label = self._history.undo()
        if label is not None:
            # Refresh opsx path if renamed (unlikely) and rewrite descriptor
            opsx_files = list(self.root.glob("*.opsx"))
            if opsx_files:
                self._opsx_path = opsx_files[0]
            self.autosave()
        return label

    def redo(self) -> str | None:
        if self._history is None:
            return None
        label = self._history.redo()
        if label is not None:
            opsx_files = list(self.root.glob("*.opsx"))
            if opsx_files:
                self._opsx_path = opsx_files[0]
            self.autosave()
        return label

    def _remember(self) -> None:
        if self._opsx_path and self._opsx_path.is_file():
            try:
                name = self.read_manifest().name
            except Exception:
                name = self._opsx_path.stem
            add_recent_project(self._opsx_path, name)

    def create(self, name: str, project_dir: Path | str) -> Path:
        """
        Create a new project in an explicit user-chosen directory.
        project_dir is the parent folder; we create project_dir/<slug>/…
        """
        if not name or not str(name).strip():
            raise ValueError("Project name is required")
        raw = str(project_dir).strip()
        if not raw:
            raise ValueError("Project folder is required — choose where to save the project")
        project_dir = Path(raw)
        slug = _safe_slug(name)
        root = project_dir / slug
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(
                f"Folder already exists and is not empty: {root}. Choose another location or name."
            )
        root.mkdir(parents=True, exist_ok=True)
        ensure_project_tree(root)

        project_id = uuid.uuid4().hex[:12]
        now = _utc_now()
        opsx_path = root / f"{slug}.opsx"
        manifest = Manifest(
            format_version=OPSX_FORMAT_VERSION,
            name=name.strip(),
            created_at=now,
            updated_at=now,
            id=project_id,
        )
        workflow = default_workflow()

        _write_json(root / "manifest.json", manifest.model_dump(mode="json"))
        _write_json(root / "workflow.json", workflow.model_dump(mode="json"))

        self._root = root
        self._opsx_path = opsx_path
        self._bind_history()
        self.autosave()
        self._remember()
        return root

    def open_opsx(self, opsx_path: Path) -> Path:
        """Open a live .opsx JSON project (or legacy zip / .opsz via import)."""
        opsx_path = Path(opsx_path)
        if not opsx_path.is_file():
            raise FileNotFoundError(f"Project not found: {opsx_path}")

        if opsx_path.suffix.lower() == ".opsz" or _is_zip_file(opsx_path):
            raise ValueError(
                "This looks like a portable archive (.opsz). "
                "Use Import .opsz to extract it into a folder, then open the .opsx inside."
            )

        data = _read_json(opsx_path)
        root = opsx_path.parent
        ensure_project_tree(root)

        if "workflow" in data:
            _write_json(root / "workflow.json", data["workflow"])
        if "manifest" in data:
            man = data["manifest"]
        else:
            man = {
                "format_version": data.get("format_version", OPSX_FORMAT_VERSION),
                "name": data.get("name", opsx_path.stem),
                "created_at": data.get("created_at", _utc_now()),
                "updated_at": data.get("updated_at", _utc_now()),
                "id": data.get("id"),
                "app": data.get("app", "OpenPVScope"),
            }
        _write_json(root / "manifest.json", man)

        self._root = root
        self._opsx_path = opsx_path
        self._bind_history()
        self.autosave()
        self._remember()
        return root

    def open_directory(self, directory: Path) -> Path:
        directory = Path(directory)
        opsx_files = list(directory.glob("*.opsx"))
        if opsx_files:
            return self.open_opsx(opsx_files[0])
        if (directory / "manifest.json").is_file():
            ensure_project_tree(directory)
            self._root = directory
            slug = _safe_slug(_read_json(directory / "manifest.json").get("name", directory.name))
            self._opsx_path = directory / f"{slug}.opsx"
            self._bind_history()
            self.autosave()
            self._remember()
            return directory
        raise ValueError(f"No .opsx project found in: {directory}")

    def autosave(self) -> Path:
        """Rewrite the .opsx descriptor from on-disk state (safe after every change)."""
        if self._root is None:
            raise RuntimeError("No project is open")
        if self._opsx_path is None:
            slug = _safe_slug(self.read_manifest().name)
            self._opsx_path = self._root / f"{slug}.opsx"

        manifest = self.read_manifest().model_dump(mode="json")
        manifest["updated_at"] = _utc_now()
        _write_json(self._root / "manifest.json", manifest)

        workflow = self.read_workflow().model_dump(mode="json")
        _write_json(self._root / "workflow.json", workflow)

        descriptor = {
            "format_version": OPSX_FORMAT_VERSION,
            "app": "OpenPVScope",
            "kind": "openpvscope-project",
            "name": manifest.get("name"),
            "id": manifest.get("id"),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest["updated_at"],
            "root": ".",
            "paths": {
                "manifest": "manifest.json",
                "workflow": "workflow.json",
                **{d.split("/")[0]: d.split("/")[0] for d in STAGE_DIRS},
                "inputs": "inputs",
                "alignment": "alignment",
                "photogrammetry": "photogrammetry",
                "detection": "detection",
                "segmentation": "segmentation",
                "labels": "labels",
                "models": "models",
                "classification": "classification",
                "exports": "exports",
                "work": "work",
            },
            "manifest": manifest,
            "workflow": workflow,
        }
        _write_json(self._opsx_path, descriptor)
        return self._opsx_path

    def save(self, opsx_path: Path | None = None) -> Path:
        """Alias for autosave (live projects don't zip on save)."""
        if opsx_path is not None:
            self._opsx_path = Path(opsx_path)
        return self.autosave()

    def export_opsz(self, dest: Path, mode: OpszMode | None = None) -> Path:
        """Zip the project folder into a portable .opsz archive (full or light)."""
        if self._root is None:
            raise RuntimeError("No project is open")
        self.autosave()
        settings = load_settings()
        mode = mode or settings.opsz_default_mode
        light_exclude = list(settings.opsz_light_exclude)

        dest = Path(dest)
        if dest.suffix.lower() != ".opsz":
            dest = dest.with_suffix(".opsz")
        dest.parent.mkdir(parents=True, exist_ok=True)

        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".opsz")
        os.close(tmp_fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w") as zf:
                for path in sorted(self._root.rglob("*")):
                    if path.is_dir():
                        continue
                    rel = path.relative_to(self._root).as_posix()
                    if _should_exclude(rel, mode, light_exclude):
                        continue
                    compress = (
                        zipfile.ZIP_STORED
                        if path.suffix.lower() in _STORE_SUFFIXES
                        else zipfile.ZIP_DEFLATED
                    )
                    zf.write(path, rel, compress_type=compress)
            if dest.exists():
                dest.unlink()
            shutil.move(str(tmp), str(dest))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return dest

    def import_opsz(self, opsz_path: Path, dest_dir: Path) -> Path:
        """Extract a .opsz archive into dest_dir and open the project."""
        opsz_path = Path(opsz_path)
        dest_dir = Path(dest_dir)
        if not opsz_path.is_file():
            raise FileNotFoundError(opsz_path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if any(dest_dir.iterdir()):
            dest_dir = dest_dir / opsz_path.stem
            dest_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(opsz_path, "r") as zf:
            zf.extractall(dest_dir)

        opsx_files = list(dest_dir.glob("*.opsx"))
        if opsx_files:
            return self.open_opsx(opsx_files[0])
        return self.open_directory(dest_dir)

    def close(self) -> None:
        if self._root is not None:
            try:
                self.autosave()
                self._remember()
            except Exception:
                pass
        self._root = None
        self._opsx_path = None
        self._history = None

    def read_manifest(self) -> Manifest:
        return Manifest.model_validate(_read_json(self.root / "manifest.json"))

    def read_workflow(self) -> Workflow:
        return Workflow.model_validate(_read_json(self.root / "workflow.json"))

    def write_workflow(self, workflow: Workflow) -> None:
        _write_json(self.root / "workflow.json", workflow.model_dump(mode="json"))
        self.autosave()

    def update_manifest_name(self, name: str) -> Manifest:
        self.checkpoint("Before rename project")
        m = self.read_manifest()
        data = m.model_dump(mode="json")
        data["name"] = name
        data["updated_at"] = _utc_now()
        _write_json(self.root / "manifest.json", data)
        self.autosave()
        return Manifest.model_validate(data)


_store: ProjectStore | None = None


def get_store() -> ProjectStore:
    global _store
    if _store is None:
        _store = ProjectStore()
    return _store
