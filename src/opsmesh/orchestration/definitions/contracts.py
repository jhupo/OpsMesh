"""Deterministic composition graphs and Workflow command values; no persistence or I/O."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FlowValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FlowLimits(FlowValue):
    max_steps: int = Field(default=256, ge=1, le=4096)
    max_depth: int = Field(default=8, ge=1, le=16)
    max_concurrency: int = Field(default=8, ge=1, le=32)
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)
    model_tokens: int = Field(default=1000000, ge=0, le=100000000)


class FlowReference(FlowValue):
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "FlowWorkflow"
    run_id: UUID
    generation: int = 1
    command_cursor: int = 0


NodeKind = Literal[
    "agent",
    "operation",
    "condition",
    "parallel",
    "join",
    "foreach",
    "repeat",
    "subflow",
    "wait",
    "expand",
]


class FlowCondition(FlowValue):
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "exists"]
    left: Any
    right: Any = None


class FlowExport(FlowValue):
    instance_id: UUID
    installation_id: UUID
    configuration_revision: int = Field(ge=0)
    artifact: str = Field(pattern="^sha256:[a-f0-9]{64}$")
    export_id: str = Field(min_length=1, max_length=100)


class FlowNode(FlowValue):
    key: str = Field(pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    kind: NodeKind
    depends_on: list[str] = Field(default_factory=list, max_length=256)
    arguments: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=dict)
    condition: FlowCondition | None = None
    run_if: FlowCondition | None = None
    export: FlowExport | None = None
    configuration_revision_id: UUID | None = None
    revision_id: UUID | None = None
    body: FlowGraph | None = None
    max_iterations: int = Field(default=1, ge=1, le=256)
    concurrency: int = Field(default=1, ge=1, le=32)
    join: Literal["all", "any", "quorum"] = "all"
    quorum: int = Field(default=1, ge=1, le=256)
    cancel_remaining: bool = True
    wait_kind: Literal["timer", "signal", "approval", "external"] = "timer"
    wait_seconds: int = Field(default=0, ge=0, le=604800)
    signal_name: str | None = Field(default=None, min_length=1, max_length=100)
    approval_id: UUID | None = None
    approver_user_id: UUID | None = None
    external_step: str | None = None
    allowed_kinds: list[NodeKind] = Field(default_factory=list, max_length=10)
    allowed_references: list[str] = Field(default_factory=list, max_length=256)
    max_expanded_nodes: int = Field(default=32, ge=1, le=256)
    approval_required: bool = False

    @model_validator(mode="after")
    def complete_contract(self) -> FlowNode:
        if self.kind == "operation" and self.export is None:
            raise ValueError("An operation requires a fixed installed export")
        if self.kind == "agent" and self.configuration_revision_id is None:
            raise ValueError("An Agent node requires an immutable configuration revision")
        if self.kind == "condition" and self.condition is None:
            raise ValueError("A condition node requires a typed comparison")
        if self.kind == "subflow" and self.revision_id is None:
            raise ValueError("A subflow requires an immutable flow revision")
        if self.kind in {"parallel", "foreach", "repeat"} and self.body is None:
            raise ValueError("A bounded composite requires its validated child graph")
        if self.kind == "wait":
            if self.wait_kind == "signal" and self.signal_name is None:
                raise ValueError("A signal wait requires its signal name")
            if self.wait_kind == "external" and self.external_step is None:
                raise ValueError("An external wait requires an owned step reference")
        if self.kind == "expand" and not self.allowed_kinds:
            raise ValueError("An expand region requires a finite node allowlist")
        if self.join != "all" and not self.cancel_remaining:
            raise ValueError(
                "Early joins require confirmed cancellation of remaining owned branches"
            )
        return self


class FlowGraph(FlowValue):
    nodes: list[FlowNode] = Field(min_length=1, max_length=256)
    output: Any = Field(default_factory=dict)


FlowNode.model_rebuild()


class FlowExecutionPolicy(FlowValue):
    """Published effects policy; retries resume receipts without resubmitting children."""

    failure: Literal["cancel_owned"] = "cancel_owned"
    retry: Literal["resume_receipts"] = "resume_receipts"
    cancellation: Literal["confirm_owned"] = "confirm_owned"
    activity_attempts: int = Field(default=3, ge=1, le=10)


class FlowDefinitionSpec(FlowValue):
    graph: FlowGraph
    limits: FlowLimits = Field(default_factory=FlowLimits)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=dict)
    template_source: str | None = Field(default=None, max_length=240)
    policy: FlowExecutionPolicy = Field(default_factory=FlowExecutionPolicy)


class FlowProjection(FlowValue):
    run_id: UUID
    driver_generation: int = Field(default=1, ge=1)
    expected_revision: int = Field(ge=0)
    projection_id: str = Field(min_length=1, max_length=240)
    status: Literal[
        "running", "waiting", "paused", "succeeded", "failed", "cancelled", "needs_attention"
    ]
    output: Any = None
    error_code: str | None = None


class StepCommand(FlowValue):
    run_id: UUID
    path: str = Field(min_length=1, max_length=500)
    node: FlowNode
    arguments: dict[str, Any]
    generation: int = Field(default=1, ge=1, le=256)
    driver_generation: int = Field(default=1, ge=1)
    skip: bool = False


class FlowSignal(FlowValue):
    command_id: UUID
    step_path: str = Field(min_length=1, max_length=500)
    generation: int = Field(ge=1, le=256)
    name: str = Field(min_length=1, max_length=100)
    value: Any = None


TERMINAL_FLOW_STATUSES = frozenset({"succeeded", "failed", "cancelled", "needs_attention"})
