from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer

from backend.app.api.schemas.redaction import redact_sensitive_payload, redact_sensitive_text


class McpToolDescriptor(BaseModel):
    server_id: UUID
    server_name: str
    tool_name: str
    description: str
    input_schema: dict[str, object]
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class McpCatalogUsageResponse(BaseModel):
    call_count: int
    failed_call_count: int
    last_call_at: datetime | None
    last_call_status: str | None
    last_error_code: str | None


class McpCatalogToolPolicySummaryResponse(BaseModel):
    timeout_seconds: int
    max_input_bytes: int
    max_output_bytes: int
    max_calls_per_run: int | None
    max_calls_per_hour: int | None
    current_hour_call_count: int
    hourly_limit_remaining: int | None
    limit_window_seconds: int


class McpCatalogToolResponse(BaseModel):
    id: UUID
    tool_name: str
    description: str
    input_schema: dict[str, object]
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]
    policy_summary: McpCatalogToolPolicySummaryResponse
    status: str
    usage: McpCatalogUsageResponse

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class McpCatalogServerResponse(BaseModel):
    id: UUID
    name: str
    server_type: str
    visibility: str
    status: str
    health_status: str
    last_health_check_at: datetime | None
    last_error: str | None
    execution_mode: str
    executable: bool
    blocked_reasons: list[str]
    credential_status: str
    credential_count: int
    workspace_credential_count: int
    connection_summary: dict[str, object]
    usage: McpCatalogUsageResponse
    tools: list[McpCatalogToolResponse]
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_error")
    def _serialize_last_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None
