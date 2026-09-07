from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.capabilities.mcp_redaction import redacted_connection
from backend.app.api.schemas.common import TimestampedModel
from backend.app.api.schemas.redaction import redact_sensitive_payload, redact_sensitive_text


class McpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    server_type: Literal["stdio", "streamable_http", "sse", "hosted"] = "stdio"
    connection: dict[str, object] = Field(default_factory=dict)
    visibility: str = Field(default="private", pattern="^(private|public)$")


class McpServerUpdateRequest(BaseModel):
    connection: dict[str, object] | None = None
    visibility: str | None = Field(default=None, pattern="^(private|public)$")


class McpServerHealthCheckRequest(BaseModel):
    health_status: str = Field(pattern="^(healthy|unhealthy|unknown)$")
    error_code: str | None = Field(default=None, max_length=120)


class McpServerResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    server_type: str
    connection: dict[str, object]
    visibility: str
    status: str
    health_status: str
    last_health_check_at: datetime | None
    last_error: str | None

    @field_serializer("connection")
    def _serialize_connection(self, connection: dict[str, object]) -> dict[str, object]:
        return redacted_connection(connection)

    @field_serializer("last_error")
    def _serialize_last_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None


class McpToolAllowRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    input_schema: dict[str, object] = Field(default_factory=dict)
    capability_key: str | None = Field(default=None, max_length=120)
    requires_approval: bool = False
    risk_level: str = Field(default="low", max_length=32)
    policy: dict[str, object] = Field(default_factory=dict)


class McpToolAllowResponse(TimestampedModel):
    workspace_id: UUID
    mcp_server_id: UUID
    tool_name: str
    description: str
    input_schema: dict[str, object]
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]
    status: str

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
