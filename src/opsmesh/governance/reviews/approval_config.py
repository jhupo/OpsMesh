"""Validated, workspace-owned approval configuration (not agent-supplied policy)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActionKind = Literal["model_request", "mcp_tool", "product_tool", "runtime_command", "resource"]
Disposition = Literal["allow", "review", "deny"]


class ReviewModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model_provider_credential_id: UUID
    model: str = Field(min_length=1)
    instructions: str = Field(min_length=1, max_length=16000)
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    max_output_tokens: int = Field(default=1200, ge=128, le=32768)
    on_error: Literal["human", "deny"] = "human"


class ApprovalRule(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128)
    action: ActionKind
    decision: Disposition
    name: str | None = None
    server_id: UUID | None = None
    visibility: str | None = None
    command_prefix: list[str] | None = None
    reviewer: Literal["human", "model"] | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> "ApprovalRule":
        if self.command_prefix is not None:
            if self.action != "runtime_command" or not self.command_prefix:
                raise ValueError("command_prefix requires runtime_command and nonempty argv")
            if any(not part or "\x00" in part for part in self.command_prefix):
                raise ValueError("command_prefix must contain valid argv tokens")
        if self.server_id is not None and self.action != "mcp_tool":
            raise ValueError("server_id is only valid for mcp_tool")
        if self.visibility is not None and self.action != "resource":
            raise ValueError("visibility is only valid for resource")
        if self.reviewer is not None and self.decision != "review":
            raise ValueError("reviewer requires decision=review")
        return self


class ApprovalConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    claude_permission_mode: Literal["default", "auto"] = "default"
    default: Disposition = "allow"
    reviewer: Literal["human", "model"] = "human"
    opaque_commands: Literal["review", "deny"] = "review"
    model: ReviewModelConfig | None = None
    rules: list[ApprovalRule] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_rules(self) -> "ApprovalConfiguration":
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("Approval rule ids must be unique")
        if (
            self.reviewer == "model" or any(r.reviewer == "model" for r in self.rules)
        ) and self.model is None:
            raise ValueError("Model review requires provider, model and instructions")
        return self


def approval_configuration(settings: dict[str, object]) -> ApprovalConfiguration:
    return ApprovalConfiguration.model_validate(settings.get("approvals", {}))
