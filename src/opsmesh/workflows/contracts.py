"""Serializable workflow graphs; no database, execution SDK or Temporal imports."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Permission = Literal["read", "use", "edit"]
Visibility = Literal["private", "public"]


class WorkflowValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(WorkflowValue):
    operator: Literal[
        "eq", "ne", "lt", "le", "gt", "ge", "contains", "in", "exists", "all", "any", "not"
    ]
    left: object = None
    right: object = None
    conditions: list[Condition] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_operands(self) -> Condition:
        if self.operator in {"all", "any", "not"}:
            if not self.conditions or (self.operator == "not" and len(self.conditions) != 1):
                raise ValueError("Boolean conditions require children; not requires exactly one")
            if self.left is not None or self.right is not None:
                raise ValueError("Boolean conditions use conditions, not comparison operands")
        elif self.conditions or "left" not in self.model_fields_set:
            raise ValueError("A comparison requires left and cannot contain child conditions")
        elif self.operator == "exists" and self.right is not None:
            raise ValueError("An exists condition only uses left")
        elif self.operator != "exists" and "right" not in self.model_fields_set:
            raise ValueError("A comparison requires right")
        return self


class WorkflowLimits(WorkflowValue):
    max_steps: int = Field(default=256, ge=1, le=4096)
    max_depth: int = Field(default=8, ge=1, le=16)
    max_concurrency: int = Field(default=8, ge=1, le=32)
    timeout_seconds: int = Field(default=3600, ge=1, le=604800)


class Node(WorkflowValue):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    depends_on: list[str] = Field(default_factory=list, max_length=256)
    arguments: dict[str, object] = Field(default_factory=dict)
    when: Condition | None = None
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)


class AgentNode(Node):
    kind: Literal["agent"] = "agent"
    input_mode: Literal["json", "messages"] = "json"
    binding: str = Field(min_length=1, max_length=100)


class ToolNode(Node):
    kind: Literal["tool"] = "tool"
    binding: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=100)


class TransformNode(Node):
    kind: Literal["transform"] = "transform"
    value: object = Field(default_factory=dict)


class ConditionNode(Node):
    kind: Literal["condition"] = "condition"
    condition: Condition


class JoinNode(Node):
    """All dependencies must finish; arguments explicitly select the joined output."""

    kind: Literal["join"] = "join"


class ParallelNode(Node):
    kind: Literal["parallel"] = "parallel"
    branches: list[WorkflowGraph] = Field(min_length=1, max_length=32)
    concurrency: int = Field(default=1, ge=1, le=32)
    join: Literal["all", "any", "quorum"] = "all"
    quorum: int | None = Field(default=None, ge=1, le=32)

    @model_validator(mode="after")
    def validate_join(self) -> ParallelNode:
        if self.join == "quorum":
            if self.quorum is None or self.quorum > len(self.branches):
                raise ValueError("A quorum must be between one and the number of branches")
        elif self.quorum is not None:
            raise ValueError("Only a quorum join accepts quorum")
        return self


class ForeachNode(Node):
    kind: Literal["foreach"] = "foreach"
    items: object
    body: WorkflowGraph
    max_items: int = Field(default=32, ge=1, le=256)
    concurrency: int = Field(default=1, ge=1, le=32)


class RepeatNode(Node):
    kind: Literal["repeat"] = "repeat"
    body: WorkflowGraph
    until: Condition
    max_iterations: int = Field(default=3, ge=1, le=256)


class SubworkflowNode(Node):
    kind: Literal["subworkflow"] = "subworkflow"
    revision_id: UUID


class WaitNode(Node):
    kind: Literal["wait"] = "wait"
    wait_kind: Literal["timer", "signal"] = "timer"
    seconds: int | None = Field(default=None, ge=0, le=604800)
    signal_name: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    timeout_seconds: int | None = Field(default=None, ge=1, le=604800)

    @model_validator(mode="after")
    def validate_wait(self) -> WaitNode:
        if self.wait_kind == "timer":
            if (
                self.seconds is None
                or self.signal_name is not None
                or self.timeout_seconds is not None
            ):
                raise ValueError("A timer requires seconds and has no signal fields")
        elif self.signal_name is None or self.timeout_seconds is None or self.seconds is not None:
            raise ValueError("A signal wait requires a name and bounded timeout, without seconds")
        return self


type WorkflowNode = Annotated[
    AgentNode
    | ToolNode
    | TransformNode
    | ConditionNode
    | JoinNode
    | ParallelNode
    | ForeachNode
    | RepeatNode
    | SubworkflowNode
    | WaitNode,
    Field(discriminator="kind"),
]


class WorkflowGraph(WorkflowValue):
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=256)
    output: object = Field(default_factory=dict)


class WorkflowSpec(WorkflowValue):
    graph: WorkflowGraph
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    limits: WorkflowLimits = Field(default_factory=WorkflowLimits)


class GraphBounds(WorkflowValue):
    steps: int = Field(ge=1)
    depth: int = Field(ge=1)


class CompiledWorkflow(WorkflowValue):
    workflow_id: UUID
    revision_id: UUID | None = None  # Draft validation has no published revision.
    digest: str
    spec: WorkflowSpec
    bounds: GraphBounds


class WorkflowPlan(WorkflowValue):
    """Detached execution input; authorization must be refreshed on admission/resume."""

    entry: CompiledWorkflow
    workflows: dict[UUID, CompiledWorkflow] = Field(default_factory=dict)
    agent_slots: list[str] = Field(default_factory=list)
    tool_slots: list[str] = Field(default_factory=list)


class AgentBinding(WorkflowValue):
    instance_id: UUID
    session_id: UUID | None = None
    max_turns: int = Field(default=32, ge=1, le=256)
    timeout_seconds: int = Field(default=600, ge=1, le=604800)


class ToolBinding(WorkflowValue):
    kind: Literal["plugin", "mcp"]
    resource_id: str = Field(min_length=1, max_length=200)
    config: dict[str, object] = Field(default_factory=dict)
    allowed_operations: list[str] = Field(min_length=1, max_length=256)
    require_approval: bool = True

    @model_validator(mode="after")
    def validate_operations(self) -> ToolBinding:
        if len(self.allowed_operations) != len(set(self.allowed_operations)) or any(
            not name.strip() or len(name) > 100 for name in self.allowed_operations
        ):
            raise ValueError("Allowed operations must be nonempty, bounded and unique")
        return self


class WorkflowBindings(WorkflowValue):
    agents: dict[str, AgentBinding] = Field(default_factory=dict)
    tools: dict[str, ToolBinding] = Field(default_factory=dict)


class PreparedWorkflow(WorkflowValue):
    plan: WorkflowPlan
    bindings: WorkflowBindings


class NodeFailure(Exception):
    """A node has a confirmed terminal failure."""


class UncertainEffects(NodeFailure):
    """A node stopped without a confirmed outcome for an external effect."""


for _node_type in (ParallelNode, ForeachNode, RepeatNode):
    _node_type.model_rebuild()
