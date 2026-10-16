"""Neutral Start/Signal/Cancel/Schedule operation contracts."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy


class ExecutionReference(BaseModel):
    """Typed owner payload; product references and validators belong to the owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkflowStartOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id_reuse_policy: WorkflowIDReusePolicy = WorkflowIDReusePolicy.REJECT_DUPLICATE
    id_conflict_policy: WorkflowIDConflictPolicy = WorkflowIDConflictPolicy.FAIL
    ignore_existing: bool = True
    start_signal: str | None = Field(default=None, min_length=1, max_length=100)
    start_signal_args: tuple[JsonValue, ...] = ()

    @model_validator(mode="after")
    def validate_start_signal(self) -> "WorkflowStartOptions":
        if self.start_signal is None and self.start_signal_args:
            raise ValueError("A start signal requires its decision reference")
        return self


class DispatchOperation(StrEnum):
    START = "start"
    SIGNAL = "signal"
    CANCEL = "cancel"


class DispatchRequest(BaseModel):
    """One durable command for a Workflow.

    ``dispatch_id`` is the stable delivery identity: a start command is
    idempotent through the Workflow id, and a signal command is delivered with
    this identity as its first argument so receivers can deduplicate
    at-least-once signal delivery (V4: signals bind command_id and waiting
    generation). Start and cancel commands for one Workflow are ordered by a
    durable decision taken under a per-Workflow lock: a cancel never publishes
    while its start is still undelivered, and a start is superseded once a
    cancel decision exists, so a cancellation can never be recorded as complete
    while the Workflow is still started afterwards.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    workflow_id: str = Field(min_length=1, max_length=255)
    operation: DispatchOperation
    execution: dict[str, JsonValue] | None = None
    workflow_name: str | None = Field(default=None, min_length=1, max_length=255)
    start_options: WorkflowStartOptions | None = None
    signal_name: str | None = Field(default=None, min_length=1, max_length=100)
    signal_args: tuple[JsonValue, ...] | None = None
    traceparent: str | None = Field(
        default=None, pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"
    )
    tracestate: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_destination(self) -> "DispatchRequest":
        if self.operation == DispatchOperation.START:
            if self.execution is None:
                raise ValueError("Start dispatch requires an owner payload")
            if self.workflow_name is None or self.start_options is None:
                raise ValueError("Start dispatch requires explicit workflow and start options")
            if self.signal_name is not None or self.signal_args is not None:
                raise ValueError("Start dispatch cannot contain a signal")
        elif self.workflow_name is not None or self.start_options is not None:
            raise ValueError("Only a start dispatch can contain workflow start options")
        elif self.operation == DispatchOperation.SIGNAL:
            if self.signal_name is None or self.signal_args is None or self.execution is not None:
                raise ValueError("Signal dispatch requires only a named decision reference")
        elif (
            self.execution is not None
            or self.signal_name is not None
            or self.signal_args is not None
        ):
            raise ValueError("Cancel dispatch cannot contain execution or signal data")
        return self
