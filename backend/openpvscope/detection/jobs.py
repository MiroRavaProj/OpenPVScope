"""Background detection job state."""

from __future__ import annotations

import traceback
import threading
from typing import Any, Literal

from openpvscope.console import get_console
from openpvscope.detection.pipeline import (
    DEFAULT_CONFIDENCE,
    DEFAULT_NMS_IOU,
    DEFAULT_NUM_TEMPLATES,
    run_detection,
)
from openpvscope.domain.models import StepStatus
from openpvscope.project.store import ProjectStore
from openpvscope.workflow import mark_step

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state: dict[str, Any] = {"running": False, "error": None, "result": None}

RunModality = Literal["rgb", "thermal", "both"]


def detection_job_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def start_detection_job(
    store: ProjectStore,
    *,
    modality: RunModality = "both",
    confidence: float = DEFAULT_CONFIDENCE,
    nms_iou: float = DEFAULT_NMS_IOU,
    num_templates: int = DEFAULT_NUM_TEMPLATES,
) -> None:
    global _thread
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Detection already running")
        _state.update({"running": True, "error": None, "result": None})

    console = get_console()
    root = store.root
    # Always run both modalities when requested; single-mod kept for API compat
    if modality == "both":
        mods: list[Literal["rgb", "thermal"]] = ["rgb", "thermal"]
    else:
        mods = [modality]  # type: ignore[list-item]

    def work() -> None:
        label = " + ".join(m.upper() for m in mods)
        console.begin_job("Panel detection", detail=f"Template matching ({label})")
        store.checkpoint("Before panel detection")
        console.log(
            f"Starting detection on {label} | confidence={confidence} nms_iou={nms_iou} templates={num_templates}",
            level="info",
            step="detection",
        )

        totals: list[dict[str, Any]] = []
        try:
            n = len(mods)
            for i, mod in enumerate(mods):
                base = (i / n) * 100.0
                span = 100.0 / n

                def progress(p: float | None, msg: str, _base=base, _span=span, _mod=mod) -> None:
                    mapped = None if p is None else _base + (p / 100.0) * _span
                    console.set_progress(
                        mapped,
                        detail=f"[{_mod}] {msg}",
                        step="detection",
                        level="info",
                    )

                def log_cb(level: str, msg: str, _mod=mod) -> None:
                    console.log(msg, level=level if level in ("info", "verbose", "warn", "error", "success") else "verbose", step="detection")

                console.log(f"=== {mod.upper()} ({i + 1}/{n}) ===", level="info", step="detection")
                result = run_detection(
                    root,
                    modality=mod,
                    confidence=confidence,
                    nms_iou=nms_iou,
                    num_templates=num_templates,
                    progress=progress,
                    log=log_cb,
                )
                totals.append(result)
                console.log(
                    f"[{mod}] finished with {result['count']} oriented panels → {result['path']}",
                    level="info",
                    step="detection",
                )

            total_count = sum(int(r.get("count") or 0) for r in totals)
            mark_step(
                store,
                "detection",
                StepStatus.DONE,
                message=f"{total_count} panels ({label})",
            )
            with _lock:
                _state.update(
                    {
                        "running": False,
                        "result": {"modalities": totals, "count": total_count},
                        "error": None,
                    }
                )
            console.end_job(ok=True, message=f"Detected {total_count} panels ({label})")
        except Exception as e:
            console.log(traceback.format_exc(), level="verbose", step="detection")
            with _lock:
                _state.update({"running": False, "error": str(e), "result": None})
            console.end_job(ok=False, message=str(e))

    _thread = threading.Thread(target=work, daemon=True)
    _thread.start()
