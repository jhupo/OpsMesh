"""Canonical node contract shared by authored and generated task plans."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt, model_validator

from backend.app.capabilities.schema_validation import reject_embedded_secrets
from backend.app.orchestration.conditions import validate_condition

ConditionOperator = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "exists",
    "not_exists",
    "in",
    "not_in",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
]
WorkflowNodeType = Literal[
    "agent", "tool", "mcp", "condition", "join", "approval", "subworkflow", "start", "end"
]


class WorkflowCondition(BaseModel):
    """A bounded, data-only condition; arbitrary Python expressions are not accepted."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all: list[WorkflowCondition] | None = Field(default=None, max_length=16)
    any: list[WorkflowCondition] | None = Field(default=None, max_length=16)
    not_: WorkflowCondition | None = Field(default=None, alias="not")
    path: str | None = Field(
        default=None,
        max_length=240,
        pattern=r"^(task\.(input|generic_state|domain_state|final_output)(\.[A-Za-z0-9_-]+)*|steps\.[A-Za-z0-9_.-]+\.(status|result_summary))$",
    )
    operator: ConditionOperator | None = None
    value: object | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> WorkflowCondition:
        variants = [
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
            self.path is not None,
        ]
        if sum(variants) != 1:
            raise ValueError("Condition must contain exactly one of all, any, not, or path")
        if self.path is not None and self.operator is None:
            raise ValueError("Leaf condition requires an operator")
        if self.path is None and (self.operator is not None or self.value is not None):
            raise ValueError("Group conditions cannot define operator or value")
        if (
            self.path is not None
            and self.operator in {"exists", "not_exists"}
            and self.value is not None
        ):
            raise ValueError("exists and not_exists conditions cannot define a value")
        if self.all is not None and not self.all:
            raise ValueError("all condition must contain at least one child")
        if self.any is not None and not self.any:
            raise ValueError("any condition must contain at least one child")
        return self


class McpToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mcp_server_id: UUID
    mcp_tool_allowlist_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=160)


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    package_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    node_type: WorkflowNodeType = "agent"
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=8_000)
    required_role: str = Field(default="specialist", min_length=1, max_length=120)
    required_skills: list[str] = Field(default_factory=list, max_length=32)
    assigned_agent_profile_id: UUID | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=128)
    required_tools: list[str] = Field(default_factory=list, max_length=32)
    required_mcp_tools: list[McpToolSelection] = Field(default_factory=list, max_length=32)
    required_resource_ids: list[UUID] = Field(default_factory=list, max_length=32)
    resource_requirements: dict[str, StrictInt] = Field(default_factory=dict, max_length=32)
    expected_artifacts: list[str] = Field(default_factory=list, max_length=32)
    acceptance_criteria: list[str] = Field(
        default_factory=lambda: ["The work package produces a clear result summary."],
        min_length=1,
        max_length=32,
    )
    review_policy: dict[str, object] = Field(default_factory=dict)
    condition: WorkflowCondition | None = None
    join_policy: Literal["all_success", "all_selected"] = "all_success"
    locked: bool = False
    tool_name: str | None = Field(default=None, min_length=1, max_length=160)
    arguments: dict[str, object] = Field(default_factory=dict, max_length=32)
    output_schema: dict[str, object] | None = None
    subworkflow_definition_id: UUID | None = None
    subworkflow_version: StrictInt | None = Field(default=None, ge=1)
    estimated_cost_usd: FiniteFloat = Field(default=0, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_node_policy(self) -> WorkflowNode:
        if self.node_type in {"tool", "mcp"} and self.tool_name is None:
            raise ValueError(f"{self.node_type} nodes require tool_name")
        if (
            self.node_type == "tool"
            and self.required_tools
            and self.tool_name not in self.required_tools
        ):
            raise ValueError("tool_name must be included in required_tools")
        if self.node_type == "mcp" and not self.required_mcp_tools:
            raise ValueError("mcp nodes require required_mcp_tools")
        if self.node_type == "mcp" and self.tool_name not in {
            item.tool_name for item in self.required_mcp_tools
        }:
            raise ValueError("tool_name must be included in required_mcp_tools")
        if self.node_type == "subworkflow" and self.subworkflow_definition_id is None:
            raise ValueError("subworkflow nodes require subworkflow_definition_id")
        if self.node_type != "subworkflow" and self.subworkflow_version is not None:
            raise ValueError("subworkflow_version is only valid for subworkflow nodes")
        if self.node_type in {"condition", "join", "start", "end"} and (
            self.tool_name is not None or self.subworkflow_definition_id is not None
        ):
            raise ValueError(f"{self.node_type} nodes cannot define execution targets")
        if self.output_schema is not None:
            from backend.app.capabilities.schema_validation import validate_json_schema

            validate_json_schema(self.output_schema)
        reject_embedded_secrets(self.arguments, path="arguments")
        for name in (
            "depends_on",
            "required_skills",
            "required_tools",
            "expected_artifacts",
            "acceptance_criteria",
        ):
            values = getattr(self, name)
            if any(not item.strip() for item in values):
                raise ValueError(f"{name} cannot contain empty items")
        for name in ("depends_on", "required_skills", "required_tools", "required_resource_ids"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} cannot contain duplicates")
        mcp_keys = {(item.mcp_server_id, item.tool_name) for item in self.required_mcp_tools}
        if len(mcp_keys) != len(self.required_mcp_tools):
            raise ValueError("MCP tool identities cannot contain duplicates")
        if any(
            not key.strip() or amount <= 0 for key, amount in self.resource_requirements.items()
        ):
            raise ValueError("Resource requirements must have names and positive amounts")
        if self.condition is not None:
            validate_condition(
                self.condition.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        reject_embedded_secrets(self.review_policy, path="review_policy")
        return self
