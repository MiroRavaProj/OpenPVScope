"""Background segmentation job."""

from __future__ import annotations

import threading
from typing import Any

from openpvscope.console import get_console
from openpvscope.domain.models import StepStatus
from openpvscope.project.store import ProjectStore
from openpvscope.segmentation.extract import SEGMENTATION_REV, run_segmentation
from openpvscope.segmentation.pairing import DEFAULT_MIN_IOU
from openpvscope.workflow import mark_step

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {"running": False, "error": None, "result": None}


def segmentation_job_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def start_segmentation_job(
    store: ProjectStore,
    *,
    margin_factor: float = 0.2,
    search_radius_m: float | None = None,
    min_iou: float = DEFAULT_MIN_IOU,
) -> None:
    global _thread
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Segmentation already running")
        _state.update({"running": True, "error": None, "result": None})

    console = get_console()

    def work() -> None:
        console.begin_job(
            "Segmentation",
            detail=f"RGB↔thermal pairing & crops [{SEGMENTATION_REV}]",
        )
        store.checkpoint("Before segmentation")

        def progress(p: float | None, msg: str) -> None:
            console.set_progress(p, detail=msg, step="segmentation", level="info")

        try:
            result = run_segmentation(
                store.root,
                margin_factor=margin_factor,
                search_radius_m=search_radius_m,
                min_iou=min_iou,
                progress=progress,
            )
            mark_step(
                store,
                "segmentation",
                StepStatus.DONE,
                message=f"{result['count']} pairs [{SEGMENTATION_REV}]",
            )
            with _lock:
                _state.update({"running": False, "result": result, "error": None})
            console.end_job(
                ok=True,
                message=f"Segmented {result['count']} pairs [{SEGMENTATION_REV}]",
            )
        except Exception as e:
            with _lock:
                _state.update({"running": False, "error": str(e), "result": None})
            console.end_job(ok=False, message=str(e))

    _thread = threading.Thread(target=work, daemon=True)
    _thread.start()
