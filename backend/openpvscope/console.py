"""In-memory activity console: logs + job progress for the UI dock."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

Level = Literal["info", "verbose", "error", "success", "warn"]


@dataclass
class ConsoleEntry:
    seq: int
    ts: float
    level: Level
    message: str
    step: str | None = None
    job_id: str | None = None


@dataclass
class JobState:
    id: str
    title: str
    status: Literal["idle", "running", "done", "error"] = "idle"
    progress: float | None = None  # 0..100, None = indeterminate
    detail: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None


class ConsoleBus:
    def __init__(self, maxlen: int = 800) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._entries: deque[ConsoleEntry] = deque(maxlen=maxlen)
        self._job: JobState | None = None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._job = None

    def log(
        self,
        message: str,
        *,
        level: Level = "info",
        step: str | None = None,
        job_id: str | None = None,
    ) -> ConsoleEntry:
        with self._lock:
            self._seq += 1
            entry = ConsoleEntry(
                seq=self._seq,
                ts=time.time(),
                level=level,
                message=message,
                step=step,
                job_id=job_id or (self._job.id if self._job else None),
            )
            self._entries.append(entry)
            return entry

    def begin_job(self, title: str, *, detail: str | None = None) -> str:
        with self._lock:
            job_id = uuid.uuid4().hex[:10]
            self._job = JobState(
                id=job_id,
                title=title,
                status="running",
                progress=None,
                detail=detail,
            )
            self._seq += 1
            self._entries.append(
                ConsoleEntry(
                    seq=self._seq,
                    ts=time.time(),
                    level="info",
                    message=title,
                    step="start",
                    job_id=job_id,
                )
            )
            return job_id

    def set_progress(
        self,
        progress: float | None,
        *,
        detail: str | None = None,
        step: str | None = None,
        level: Level = "verbose",
        log_entry: bool = True,
    ) -> None:
        with self._lock:
            if self._job is None or self._job.status != "running":
                return
            if progress is not None:
                self._job.progress = max(0.0, min(100.0, float(progress)))
            if detail is not None:
                self._job.detail = detail
            if not log_entry or not (detail or step):
                return
            msg = detail or step or ""
            # Skip duplicate consecutive lines (poll races / double callbacks)
            if self._entries and self._entries[-1].message == msg and self._entries[-1].job_id == self._job.id:
                return
            self._seq += 1
            self._entries.append(
                ConsoleEntry(
                    seq=self._seq,
                    ts=time.time(),
                    level=level,
                    message=msg,
                    step=step,
                    job_id=self._job.id,
                )
            )

    def end_job(self, *, ok: bool = True, message: str | None = None) -> None:
        with self._lock:
            if self._job is None:
                return
            self._job.status = "done" if ok else "error"
            self._job.progress = 100.0 if ok else self._job.progress
            self._job.ended_at = time.time()
            if message:
                self._job.detail = message
            self._seq += 1
            self._entries.append(
                ConsoleEntry(
                    seq=self._seq,
                    ts=time.time(),
                    level="success" if ok else "error",
                    message=message or ("Completed" if ok else "Failed"),
                    step="end",
                    job_id=self._job.id,
                )
            )

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            entries = [
                {
                    "seq": e.seq,
                    "ts": e.ts,
                    "level": e.level,
                    "message": e.message,
                    "step": e.step,
                    "job_id": e.job_id,
                }
                for e in self._entries
                if e.seq > since
            ]
            job = None
            if self._job is not None:
                job = {
                    "id": self._job.id,
                    "title": self._job.title,
                    "status": self._job.status,
                    "progress": self._job.progress,
                    "detail": self._job.detail,
                    "started_at": self._job.started_at,
                    "ended_at": self._job.ended_at,
                }
            return {
                "seq": self._seq,
                "entries": entries,
                "job": job,
            }


_bus = ConsoleBus()


def get_console() -> ConsoleBus:
    return _bus
