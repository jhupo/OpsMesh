from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload

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


class OrchestrationCondition(BaseModel):
    """A bounded, data-only condition; arbitrary Python expressions are not accepted."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all: list[OrchestrationCondition] | None = Field(default=None, max_length=16)
    any: list[OrchestrationCondition] | None = Field(default=None, max_length=16)
    not_: OrchestrationCondition | None = Field(default=None, alias="not")
    path: str | None = Field(
        default=None,
        max_length=240,
        pattern=r"^(task\.(input|generic_state|domain_state|final_output)(\.[A-Za-z0-9_-]+)*|steps\.[A-Za-z0-9_.-]+\.(status|result_summary))$",
    )
    operator: ConditionOperator | None = None
    value: object | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> OrchestrationCondition:
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


class OrchestrationMcpToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mcp_server_id: UUID
    mcp_tool_allowlist_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=160)


class OrchestrationNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    node_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=8_000)
    required_role: str = Field(default="specialist", min_length=1, max_length=120)
    required_skills: list[str] = Field(default_factory=list, max_length=32)
    assigned_agent_profile_id: UUID | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=128)
    required_tools: list[str] = Field(default_factory=list, max_length=32)
    mcp_tools: list[OrchestrationMcpToolSelection] = Field(default_factory=list, max_length=32)
    required_resource_ids: list[UUID] = Field(default_factory=list, max_length=32)
    resource_requirements: dict[str, int] = Field(default_factory=dict, max_length=32)
    expected_artifacts: list[str] = Field(default_factory=list, max_length=32)
    acceptance_criteria: list[str] = Field(
        default_factory=lambda: ["The work package produces a clear result summary."],
        min_length=1,
        max_length=32,
    )
    review_policy: dict[str, object] = Field(default_factory=dict)
    condition: OrchestrationCondition | None = None
    estimated_cost_usd: float = Field(default=0, ge=0, le=1_000_000)


class OrchestrationDefinitionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    nodes: list[OrchestrationNode] = Field(min_length=1, max_length=128)


class OrchestrationDefinitionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    nodes: list[OrchestrationNode] | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_change(self) -> OrchestrationDefinitionUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("At least one orchestration field is required")
        null_fields = sorted(
            field for field in self.model_fields_set if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Orchestration fields cannot be null: {', '.join(null_fields)}")
        return self


class OrchestrationApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orchestration_definition_id: UUID
    orchestration_version: int | None = Field(default=None, ge=1)
    enqueue: bool = False


class OrchestrationDefinitionResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    key: str
    name: str
    description: str
    definition: dict[str, object]
    version: int
    status: str
    published_at: datetime | None

    @field_serializer("definition")
    def _serialize_definition(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class OrchestrationValidationResponse(BaseModel):
    workspace_id: UUID
    orchestration_definition_id: UUID
    version: int
    valid: bool
    errors: list[str]
    definition: dict[str, object]

    @field_serializer("definition")
    def _serialize_definition(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
