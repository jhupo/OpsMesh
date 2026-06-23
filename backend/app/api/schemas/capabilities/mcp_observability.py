from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.api.schemas.common import ORMModel
from backend.app.api.schemas.redaction import redact_sensitive_payload


class McpToolCallLogRequest(BaseModel):
    mcp_server_id: UUID | None = None
    agent_run_id: UUID | None = None
    approval_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=32)
    request: dict[str, object] = Field(default_factory=dict)
    response: dict[str, object] | None = None
    error: dict[str, object] | None = None


class McpToolCallLogResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    mcp_server_id: UUID | None
    agent_run_id: UUID | None
    task_id: UUID | None
    task_step_id: UUID | None
    agent_profile_id: UUID | None
    approval_id: UUID | None
    tool_name: str
    status: str
    latency_ms: int | None
    argument_sha256: str | None
    response_sha256: str | None
    error_code: str | None
    request: dict[str, object]
    response: dict[str, object] | None
    error: dict[str, object] | None
    created_at: datetime

    @field_serializer("request")
    def _serialize_request(self, request: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(request)

    @field_serializer("response")
    def _serialize_response(
        self,
        response: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(response) if response is not None else None

    @field_serializer("error")
    def _serialize_error(self, error: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(error) if error is not None else None
