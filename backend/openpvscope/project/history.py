"""Undo / redo via content-addressable history (git-style blobs).

Layout
------
.openpvscope_history/
  objects/ab/cdef…     unique file bytes keyed by sha256 (stored once)
  snapshots/000042.json  tiny manifest: path → hash
  index.json             undo / redo stacks

Unchanged rasters across checkpoints share one object → no multi-GB duplicates.

GC (garbage collection)
-----------------------
When undo steps are trimmed or redo is discarded, snapshot manifests are removed.
``gc()`` then deletes any object hash no longer referenced by a remaining snapshot.

Hardlinks (Windows / NTFS)
--------------------------
A hardlink is a second directory entry for the same bytes on disk (same volume).
We use hardlinks only when restoring *non-raster* files (JSON), where writes go
through temp+replace and will not truncate the shared object. Rasters are always
copied on restore because GeoTIFF writers often truncate in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openpvscope.io_atomic import atomic_write_json, copy_file_atomic
from openpvscope.settings import load_settings

HISTORY_DIR = ".openpvscope_history"
_HASH_BUF = 1024 * 1024
_RASTER_SUFFIXES = {".tif", ".tiff", ".geotiff", ".img", ".jp2"}

_META_GLOBS = (
    "workflow.json",
    "manifest.json",
    "*.opsx",
    "alignment/*.json",
)

_RASTER_PATHS = (
    "inputs/ortho/rgb.tif",
    "inputs/ortho/thermal.tif",
    "inputs/ortho/thermal_aligned.tif",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_HASH_BUF)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_raster(rel: str) -> bool:
    return Path(rel).suffix.lower() in _RASTER_SUFFIXES


@dataclass
class HistoryStatus:
    can_undo: bool
    can_redo: bool
    undo_label: str | None
    redo_label: str | None
    depth: int
    redo_depth: int


class ProjectHistory:
    """
    Linear undo/redo for project metadata + orthophoto rasters (default).

    Call ``checkpoint(label)`` *before* mutating the project.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.base = self.root / HISTORY_DIR
        self.objects = self.base / "objects"
        self.snapshots = self.base / "snapshots"
        self.index_path = self.base / "index.json"
        self._suppress = False
        self._ensure_layout()
        self._migrate_legacy_if_needed()

    def _ensure_layout(self) -> None:
        self.objects.mkdir(parents=True, exist_ok=True)
        self.snapshots.mkdir(parents=True, exist_ok=True)

    def _migrate_legacy_if_needed(self) -> None:
        """Drop old full-tree snapshot folders (pre-CAS) to reclaim disk."""
        legacy_dirs = [
            p
            for p in self.snapshots.iterdir()
            if p.is_dir() and (p / "_meta.json").is_file()
        ]
        if not legacy_dirs:
            return
        for p in legacy_dirs:
            shutil.rmtree(p, ignore_errors=True)
        # Reset stacks that pointed at deleted full copies
        index = self._load_index()
        index["undo"] = []
        index["redo"] = []
        index["format"] = 2
        self._save_index(index)
        self.gc()

    def _load_index(self) -> dict:
        if not self.index_path.is_file():
            return {"format": 2, "undo": [], "redo": [], "seq": 0}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            data.setdefault("format", 2)
            data.setdefault("undo", [])
            data.setdefault("redo", [])
            data.setdefault("seq", 0)
            return data
        except Exception:
            return {"format": 2, "undo": [], "redo": [], "seq": 0}

    def _save_index(self, index: dict) -> None:
        self._ensure_layout()
        index["format"] = 2
        atomic_write_json(self.index_path, index)

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest[2:]

    def _tracked_relpaths(self, *, include_rasters: bool) -> list[str]:
        found: list[str] = []
        for pattern in _META_GLOBS:
            for p in self.root.glob(pattern):
                if p.is_file() and HISTORY_DIR not in p.parts:
                    found.append(p.relative_to(self.root).as_posix())
        if include_rasters:
            for rel in _RASTER_PATHS:
                p = self.root / rel
                if p.is_file():
                    found.append(rel)
        return sorted(set(found))

    def _store_blob(self, src: Path) -> str:
        """Hash file and copy into object store if new. Returns hex digest."""
        digest = _sha256_file(src)
        dest = self._object_path(digest)
        if dest.is_file():
            return digest
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Always copy into the store — never hardlink from the live working tree,
        # because later in-place GeoTIFF writes would corrupt the shared bytes.
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return digest

    def _materialize(self, digest: str, dest: Path, *, rel: str) -> None:
        """Restore blob to dest — hardlink when safe, else copy."""
        src = self._object_path(digest)
        if not src.is_file():
            raise FileNotFoundError(f"History object missing: {digest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()

        # Hardlink JSON/sidecars only. Rasters are copied — GeoTIFF writers often
        # truncate in place, which would corrupt a shared object inode.
        if not _is_raster(rel):
            try:
                os.link(src, dest)
                return
            except OSError:
                pass
        copy_file_atomic(src, dest)

    def _write_snapshot(self, snap_id: str, label: str) -> dict:
        settings = load_settings()
        rels = self._tracked_relpaths(include_rasters=settings.history_include_rasters)
        files: dict[str, str] = {}
        for rel in rels:
            src = self.root / rel
            if src.is_file():
                files[rel] = self._store_blob(src)
        meta = {
            "version": 2,
            "id": snap_id,
            "label": label,
            "created_at": _utc_now(),
            "files": files,
        }
        atomic_write_json(self.snapshots / f"{snap_id}.json", meta)
        return meta

    def _load_snapshot(self, snap_id: str) -> dict:
        path = self.snapshots / f"{snap_id}.json"
        if not path.is_file():
            # Legacy folder snapshot — unsupported after migration
            raise FileNotFoundError(f"History snapshot missing: {snap_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _restore_snapshot(self, snap_id: str) -> None:
        meta = self._load_snapshot(snap_id)
        files: dict[str, str] = dict(meta.get("files") or {})
        settings = load_settings()
        for rel in self._tracked_relpaths(include_rasters=settings.history_include_rasters):
            cur = self.root / rel
            if cur.is_file() and rel not in files:
                try:
                    cur.unlink()
                except OSError:
                    pass
        for rel, digest in files.items():
            self._materialize(digest, self.root / rel, rel=rel)

    def _delete_snapshot(self, snap_id: str) -> None:
        path = self.snapshots / f"{snap_id}.json"
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
        legacy = self.snapshots / snap_id
        if legacy.is_dir():
            shutil.rmtree(legacy, ignore_errors=True)

    def _referenced_digests(self, index: dict | None = None) -> set[str]:
        index = index or self._load_index()
        refs: set[str] = set()
        for entry in list(index.get("undo") or []) + list(index.get("redo") or []):
            sid = entry.get("id")
            if not sid:
                continue
            snap_path = self.snapshots / f"{sid}.json"
            if not snap_path.is_file():
                continue
            try:
                meta = json.loads(snap_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for digest in (meta.get("files") or {}).values():
                if isinstance(digest, str) and len(digest) == 64:
                    refs.add(digest)
        return refs

    def gc(self) -> dict:
        """Delete object blobs not referenced by any undo/redo snapshot."""
        refs = self._referenced_digests()
        removed = 0
        freed = 0
        if not self.objects.is_dir():
            return {"removed": 0, "freed_bytes": 0}
        for prefix in self.objects.iterdir():
            if not prefix.is_dir():
                continue
            for obj in list(prefix.iterdir()):
                if not obj.is_file():
                    continue
                digest = prefix.name + obj.name
                if digest in refs:
                    continue
                try:
                    freed += obj.stat().st_size
                    obj.unlink()
                    removed += 1
                except OSError:
                    pass
            # remove empty prefix dirs
            try:
                next(prefix.iterdir())
            except StopIteration:
                try:
                    prefix.rmdir()
                except OSError:
                    pass
        return {"removed": removed, "freed_bytes": freed}

    def _trim(self, index: dict) -> None:
        settings = load_settings()
        max_steps = max(1, settings.history_max_steps)
        while len(index["undo"]) > max_steps:
            old = index["undo"].pop(0)
            self._delete_snapshot(old["id"])

    def status(self) -> HistoryStatus:
        index = self._load_index()
        undo = index.get("undo") or []
        redo = index.get("redo") or []
        return HistoryStatus(
            can_undo=len(undo) > 0,
            can_redo=len(redo) > 0,
            undo_label=undo[-1]["label"] if undo else None,
            redo_label=redo[-1]["label"] if redo else None,
            depth=len(undo),
            redo_depth=len(redo),
        )

    def checkpoint(self, label: str) -> None:
        if self._suppress:
            return
        index = self._load_index()
        index["seq"] = int(index.get("seq") or 0) + 1
        snap_id = f"{index['seq']:06d}"
        meta = self._write_snapshot(snap_id, label)
        index.setdefault("undo", []).append({"id": snap_id, "label": meta["label"]})
        for entry in index.get("redo") or []:
            self._delete_snapshot(entry["id"])
        index["redo"] = []
        self._trim(index)
        self._save_index(index)
        self.gc()

    def undo(self) -> str | None:
        index = self._load_index()
        undo = index.get("undo") or []
        if not undo:
            return None
        entry = undo.pop()
        index["seq"] = int(index.get("seq") or 0) + 1
        redo_id = f"{index['seq']:06d}"
        self._write_snapshot(redo_id, f"Redo point ({entry['label']})")
        index.setdefault("redo", []).append({"id": redo_id, "label": entry["label"]})
        self._suppress = True
        try:
            self._restore_snapshot(entry["id"])
        finally:
            self._suppress = False
        self._delete_snapshot(entry["id"])
        index["undo"] = undo
        self._save_index(index)
        self.gc()
        return entry["label"]

    def redo(self) -> str | None:
        index = self._load_index()
        redo = index.get("redo") or []
        if not redo:
            return None
        entry = redo.pop()
        index["seq"] = int(index.get("seq") or 0) + 1
        undo_id = f"{index['seq']:06d}"
        self._write_snapshot(undo_id, entry["label"])
        index.setdefault("undo", []).append({"id": undo_id, "label": entry["label"]})
        self._suppress = True
        try:
            self._restore_snapshot(entry["id"])
        finally:
            self._suppress = False
        self._delete_snapshot(entry["id"])
        index["redo"] = redo
        self._trim(index)
        self._save_index(index)
        self.gc()
        return entry["label"]

    def clear(self) -> None:
        if self.base.exists():
            shutil.rmtree(self.base, ignore_errors=True)
        self._ensure_layout()
