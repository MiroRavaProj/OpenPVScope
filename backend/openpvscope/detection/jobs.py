"""Background detection job state."""

from __future__ import annotations

import threading
from typing import Any

from openpvscope.console import get_console
from openpvscope.detection.pipeline import run_detection
from openpvscope.domain.models import StepStatus
from openpvscope.project.store import ProjectStore
from openpvscope.workflow import mark_step

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {"running": False, "error": None, "result": None}


def detection_job_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def start_detection_job(
    store: ProjectStore,
    *,
    confidence: float,
    nms_iou: float,
) -> None:
    global _thread
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Detection already running")
        _state.update({"running": True, "error": None, "result": None})

    console = get_console()
    root = store.root

    def work() -> None:
        console.begin_job("Panel detection", detail="Template matching")
        store.checkpoint("Before panel detection")

        def progress(p: float | None, msg: str) -> None:
            console.set_progress(p, detail=msg, step="detection", level="info")

        try:
            result = run_detection(
                root, confidence=confidence, nms_iou=nms_iou, progress=progress
            )
            mark_step(
                store,
                "detection",
                StepStatus.DONE,
                message=f"{result['count']} panels",
            )
            with _lock:
                _state.update({"running": False, "result": result, "error": None})
            console.end_job(ok=True, message=f"Detected {result['count']} panels")
        except Exception as e:
            with _lock:
                _state.update({"running": False, "error": str(e), "result": None})
            console.end_job(ok=False, message=str(e))

    _thread = threading.Thread(target=work, daemon=True)
    _thread.start()
