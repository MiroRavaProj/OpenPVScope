"""Workflow step transitions."""

from __future__ import annotations

from datetime import datetime, timezone

from openpvscope.domain.models import (
    PIPELINE_STEPS,
    PipelineStep,
    StepState,
    StepStatus,
    Workflow,
)
from openpvscope.project.paths import ortho_rgb, ortho_thermal, ortho_thermal_aligned
from openpvscope.project.store import ProjectStore


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def mark_step(
    store: ProjectStore,
    step: PipelineStep,
    status: StepStatus,
    *,
    skipped: bool = False,
    message: str | None = None,
) -> Workflow:
    wf = store.read_workflow()
    state = StepState(status=status, skipped=skipped, message=message, updated_at=_now())
    wf.set_step(step, state)
    # Activate next pending step when completing
    if status in (StepStatus.DONE, StepStatus.SKIPPED):
        idx = PIPELINE_STEPS.index(step)
        for nxt in PIPELINE_STEPS[idx + 1 :]:
            cur = wf.get(nxt)
            if cur.status == StepStatus.PENDING:
                wf.set_step(
                    nxt,
                    StepState(status=StepStatus.ACTIVE, updated_at=_now()),
                )
                break
    store.write_workflow(wf)
    return wf


def orthos_ready(store: ProjectStore) -> bool:
    root = store.root
    return ortho_rgb(root).is_file() and (
        ortho_thermal_aligned(root).is_file() or ortho_thermal(root).is_file()
    )


def skip_photogrammetry_with_geotiffs(
    store: ProjectStore,
    rgb_path,
    thermal_path,
) -> Workflow:
    """Copy provided GeoTIFFs into project and mark photogrammetry skipped."""
    import shutil
    from pathlib import Path

    root = store.root
    rgb_dest = ortho_rgb(root)
    thermal_dest = ortho_thermal(root)
    shutil.copy2(Path(rgb_path), rgb_dest)
    shutil.copy2(Path(thermal_path), thermal_dest)
    return mark_step(
        store,
        "photogrammetry",
        StepStatus.SKIPPED,
        skipped=True,
        message="Imported existing GeoTIFF orthophotos",
    )
