"""The PM workflow owns its output contract; SDKs own JSON-schema generation/validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.agents.execution.contracts import AgentOutputSchemaSpec


class ManagerAcceptanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "request_revision", "add_missing_work"]
    summary: str = Field(min_length=1)
    reasons: list[str] = Field(default_factory=list)
    revision_requests: list[dict[str, object]] = Field(default_factory=list)
    missing_work_packages: list[dict[str, object]] = Field(default_factory=list)


def acceptance_output_schema() -> AgentOutputSchemaSpec:
    return AgentOutputSchemaSpec(
        name="pm_acceptance",
        version="1",
        strict=False,
        schema=ManagerAcceptanceResult.model_json_schema(),
    )
