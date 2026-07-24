"""Background detection job state."""

from __future__ import annotations

import traceback
import threading
from pathlib import Path
from typing import Any, Literal

from openpvscope.console import get_console
from openpvscope.detection.pipeline import (
    DEFAULT_CONFIDENCE,
    DEFAULT_NMS_IOU,
    DEFAULT_NUM_TEMPLATES,
    DEFAULT_THERMAL_TEMP_CAP,
    PIPELINE_REV,
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
    confidence_rgb: float = DEFAULT_CONFIDENCE,
    confidence_thermal: float = DEFAULT_CONFIDENCE,
    nms_iou: float = DEFAULT_NMS_IOU,
    num_templates: int = DEFAULT_NUM_TEMPLATES,
    thermal_temp_cap: float | None = DEFAULT_THERMAL_TEMP_CAP,
    advanced_validation: bool = True,
    fine_tuning_confidence: float = 0.65,
) -> None:
    global _thread
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Detection already running")
        _state.update({"running": True, "error": None, "result": None})

    console = get_console()
    root = store.root
    if modality == "both":
        mods: list[Literal["rgb", "thermal"]] = ["rgb", "thermal"]
    else:
        mods = [modality]  # type: ignore[list-item]

    def work() -> None:
        label = " + ".join(m.upper() for m in mods)
        tpl_label = "ALL" if num_templates <= 0 else str(num_templates)
        console.begin_job("Panel detection", detail=f"Template matching ({label})")
        store.checkpoint("Before panel detection")
        console.log(
            f"Starting {label} | rgb_conf={confidence_rgb} thermal_conf={confidence_thermal} "
            f"nms={nms_iou} templates={tpl_label} thermal_cap={thermal_temp_cap} "
            f"refine={advanced_validation} ft_conf={fine_tuning_confidence} | rev={PIPELINE_REV}",
            level="info",
            step="detection",
        )

        totals: list[dict[str, Any]] = []
        try:
            n = len(mods)
            for i, mod in enumerate(mods):
                base = (i / n) * 100.0
                span = 100.0 / n
                conf = confidence_thermal if mod == "thermal" else confidence_rgb

                def progress(
                    p: float | None,
                    msg: str,
                    *,
                    level: str = "info",
                    _base=base,
                    _span=span,
                    _mod=mod,
                ) -> None:
                    mapped = None if p is None else _base + (p / 100.0) * _span
                    lvl = level if level in ("info", "verbose", "warn", "error", "success") else "verbose"
                    console.set_progress(
                        mapped,
                        detail=f"[{_mod}] {msg}",
                        step="detection",
                        level=lvl,  # type: ignore[arg-type]
                    )

                def log_cb(level: str, msg: str) -> None:
                    lvl = level if level in ("info", "verbose", "warn", "error", "success") else "verbose"
                    console.log(msg, level=lvl, step="detection")

                console.log(
                    f"=== {mod.upper()} ({i + 1}/{n}) conf={conf} ===",
                    level="info",
                    step="detection",
                )
                result = run_detection(
                    root,
                    modality=mod,
                    confidence=conf,
                    nms_iou=nms_iou,
                    num_templates=num_templates,
                    thermal_temp_cap=thermal_temp_cap if mod == "thermal" else None,
                    advanced_validation=advanced_validation,
                    fine_tuning_confidence=fine_tuning_confidence,
                    progress=progress,
                    log=log_cb,
                )
                totals.append(result)
                console.log(
                    f"[{mod}] finished with {result['count']} panels → {result['path']}",
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
