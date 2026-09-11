"""Validated application commands shared by all orchestration entry points."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.planning.workflow_contracts import WorkflowNode


class OrchestrationDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=128)


class OrchestrationDefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    nodes: list[WorkflowNode] | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_change(self) -> OrchestrationDefinitionUpdate:
        if not self.model_fields_set - {"expected_version"}:
            raise ValueError("At least one orchestration field is required")
        null_fields = sorted(
            field for field in self.model_fields_set if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Orchestration fields cannot be null: {', '.join(null_fields)}")
        return self
