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
    checkpoint: bool = True,
    unlock_next: bool = True,
) -> Workflow:
    if checkpoint:
        store.checkpoint(f"Before {step} → {status.value}")
    wf = store.read_workflow()
    previous = wf.get(step)
    already_complete = previous.status in (StepStatus.DONE, StepStatus.SKIPPED)

    state = StepState(status=status, skipped=skipped, message=message, updated_at=_now())
    wf.set_step(step, state)

    # Unlock the immediate next step only on the *first* completion.
    # Re-saving an already-done step (e.g. re-confirm alignment) must not
    # cascade and activate Detection → Segmentation → …
    if (
        unlock_next
        and status in (StepStatus.DONE, StepStatus.SKIPPED)
        and not already_complete
    ):
        idx = PIPELINE_STEPS.index(step)
        if idx + 1 < len(PIPELINE_STEPS):
            nxt = PIPELINE_STEPS[idx + 1]
            cur = wf.get(nxt)
            if cur.status == StepStatus.PENDING:
                wf.set_step(
                    nxt,
                    StepState(status=StepStatus.ACTIVE, updated_at=_now()),
                )
    store.write_workflow(wf)
    return wf


def orthos_ready(store: ProjectStore) -> bool:
    """True when enough orthophotos exist to continue past photogrammetry."""
    root = store.root
    try:
        from openpvscope.photogrammetry.setup import load_setup

        setup = load_setup(root)
        if setup.get("modalities") == "thermal_only":
            return ortho_thermal(root).is_file() or ortho_thermal_aligned(root).is_file()
    except Exception:
        pass
    return ortho_rgb(root).is_file() and (
        ortho_thermal_aligned(root).is_file() or ortho_thermal(root).is_file()
    )


def skip_alignment_for_thermal_only(store: ProjectStore) -> Workflow:
    """Mark alignment skipped so detection unlocks (thermal-only projects)."""
    return mark_step(
        store,
        "alignment",
        StepStatus.SKIPPED,
        skipped=True,
        message="Alignment skipped (thermal-only project)",
        checkpoint=False,
    )


def skip_photogrammetry_with_geotiffs(
    store: ProjectStore,
    rgb_path,
    thermal_path,
) -> Workflow:
    """Copy provided GeoTIFFs into project and mark photogrammetry skipped/done."""
    import shutil
    from pathlib import Path

    store.checkpoint("Before import GeoTIFFs")
    root = store.root
    thermal_dest = ortho_thermal(root)
    shutil.copy2(Path(thermal_path), thermal_dest)

    has_rgb = rgb_path is not None and str(rgb_path).strip()
    if has_rgb:
        rgb_dest = ortho_rgb(root)
        shutil.copy2(Path(rgb_path), rgb_dest)
        return mark_step(
            store,
            "photogrammetry",
            StepStatus.SKIPPED,
            skipped=True,
            message="Imported existing GeoTIFF orthophotos",
            checkpoint=False,
        )

    # Thermal-only import: unlock detection by skipping alignment
    mark_step(
        store,
        "photogrammetry",
        StepStatus.DONE,
        skipped=False,
        message="Imported thermal GeoTIFF (thermal-only)",
        checkpoint=False,
        unlock_next=True,
    )
    return skip_alignment_for_thermal_only(store)
