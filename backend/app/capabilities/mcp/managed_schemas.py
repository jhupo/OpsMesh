from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.capabilities.mcp.transport.stdio_credentials import _merge_environment
from backend.app.shared.contracts import TimestampedModel


class McpRestartPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_restarts: int = Field(default=5, ge=0, le=10000)
    stable_after_seconds: float = Field(default=300, gt=0)
    initial_backoff_seconds: float = Field(default=2, gt=0)
    max_backoff_seconds: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def validate_backoff(self) -> "McpRestartPolicy":
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("Maximum backoff must not be smaller than initial backoff")
        return self


class StdioProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restart_policy: McpRestartPolicy = Field(default_factory=McpRestartPolicy)
    command: str = Field(min_length=1, max_length=512)
    type: Literal["stdio"] = Field(default="stdio", exclude=True)
    args: list[str] = Field(default_factory=list, max_length=128)
    env: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    cwd: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_config(self) -> "StdioProjectConfig":
        from pathlib import PureWindowsPath

        from backend.app.capabilities.references.schema import reject_embedded_secrets

        if any("\x00" in value for value in [self.command, *self.args, self.cwd or ""]):
            raise ValueError("MCP command cannot contain null bytes")
        if any(
            PureWindowsPath(value).drive for value in [self.command, *self.args, self.cwd or ""]
        ):
            raise ValueError(
                "MCP paths must exist inside the Linux Runtime; Windows drives are unavailable"
            )
        if self.cwd is not None and not self.cwd.startswith("/"):
            raise ValueError("MCP cwd must be an absolute Runtime path")
        reject_embedded_secrets({"command": self.command, "args": self.args}, path="mcp")
        try:
            raw_environment: dict[object, object] = {
                name: value for name, value in self.env.items()
            }
            _merge_environment({}, raw_environment)
        except RuntimeError as exc:
            raise ValueError("Invalid MCP environment mapping") from exc
        if sum(len(arg.encode()) for arg in self.args) > 64_000:
            raise ValueError("MCP command is too large")
        return self


class ManagedMcpCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_id: UUID
    mcpServers: dict[str, StdioProjectConfig] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_names(self) -> "ManagedMcpCreateRequest":
        if any(not name.strip() or len(name) > 160 for name in self.mcpServers):
            raise ValueError("MCP names must contain 1 to 160 characters")
        return self


class ManagedMcpActionRequest(BaseModel):
    action: Literal["start", "stop", "restart", "refresh"]


class ManagedMcpHostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_id: UUID


class ManagedMcpResponse(TimestampedModel):
    workspace_id: UUID
    mcp_server_id: UUID
    runtime_id: UUID
    status: str
    action: str
    generation: int
    last_error: str | None
    checked_at: datetime | None
