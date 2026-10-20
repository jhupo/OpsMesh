"""Composition-owned state and explicit execution operations."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.orchestration.definitions.contracts import FlowGraph, FlowValue
from opsmesh.orchestration.execution.contracts import Configuration


class FlowRunCreate(FlowValue):
    revision_id: UUID
    configuration_id: UUID | None = None
    input: dict[str, Any] = Field(default_factory=dict)


class FlowRunView(FlowValue):
    id: UUID
    revision_id: UUID | None
    configuration_id: UUID | None
    initiated_by_id: UUID
    parent_id: UUID | None
    root_id: UUID
    status: str
    revision: int
    output: Any = None
    error_code: str | None


class FlowControl(FlowValue):
    command_id: UUID
    expected_revision: int = Field(ge=0)
    action: Literal["pause", "resume", "cancel", "retry"]


class FlowPlanProposal(FlowValue):
    command_id: UUID
    expected_revision: int = Field(ge=0)
    step_path: str = Field(min_length=1, max_length=500)
    source_invocation_id: UUID
    graph: FlowGraph
    approval_id: UUID | None = None


class FlowPlanReview(FlowPlanProposal):
    approver_user_id: UUID | None = None


class FlowPlanReviewView(FlowValue):
    approval_id: UUID
    status: str


class FlowStepView(FlowValue):
    path: str
    generation: int
    status: str
    child_kind: str | None
    child_id: UUID | None
    error_code: str | None


class FlowEventView(FlowValue):
    sequence: int
    kind: str
    summary: dict[str, Any]


class RunView(BaseModel):
    run_id: UUID
    session_id: UUID
    sequence: int
    status: str
    output: Any = None
    artifacts: list[UUID] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    error_code: str | None
    revision: int
    created_at: datetime


class EventView(BaseModel):
    sequence: int
    kind: str
    payload: dict[str, Any]


class EventPage(BaseModel):
    events: list[EventView]
    next_cursor: int


class ResumeRequest(Configuration):
    command_id: UUID
    approval_id: UUID
    expected_revision: int = Field(ge=0)


class CancelRequest(Configuration):
    command_id: UUID
    expected_revision: int = Field(ge=0)


class OperationView(BaseModel):
    operation_id: str
    kind: str
    status: str
    accounted: bool
    usage: dict[str, int]


class OperationConfirmation(Configuration):
    operation_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["succeeded", "not_executed"]
    usage: NormalizedUsage | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class ReconcileRequest(Configuration):
    command_id: UUID
    expected_revision: int = Field(ge=0)
    evidence: dict[str, Any] = Field(min_length=1)
    operations: list[OperationConfirmation] = Field(default_factory=list)
