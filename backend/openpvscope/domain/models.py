from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


OPSX_FORMAT_VERSION = 2

PipelineStep = Literal[
    "photogrammetry",
    "alignment",
    "detection",
    "segmentation",
    "models",
    "classification",
    "outputs",
]

PIPELINE_STEPS: tuple[PipelineStep, ...] = (
    "photogrammetry",
    "alignment",
    "detection",
    "segmentation",
    "models",
    "classification",
    "outputs",
)


class StepStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"


class StepState(BaseModel):
    status: StepStatus = StepStatus.PENDING
    skipped: bool = False
    message: str | None = None
    updated_at: str | None = None


class Manifest(BaseModel):
    format_version: int = OPSX_FORMAT_VERSION
    name: str
    created_at: str
    updated_at: str
    app: str = "OpenPVScope"
    id: str | None = None


class Workflow(BaseModel):
    photogrammetry: StepState = Field(default_factory=StepState)
    alignment: StepState = Field(default_factory=StepState)
    detection: StepState = Field(default_factory=StepState)
    segmentation: StepState = Field(default_factory=StepState)
    models: StepState = Field(default_factory=StepState)
    classification: StepState = Field(default_factory=StepState)
    outputs: StepState = Field(default_factory=StepState)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def get(self, step: PipelineStep) -> StepState:
        return getattr(self, step)

    def set_step(self, step: PipelineStep, state: StepState) -> None:
        setattr(self, step, state)


def default_workflow() -> Workflow:
    wf = Workflow()
    wf.photogrammetry = StepState(status=StepStatus.ACTIVE)
    return wf
