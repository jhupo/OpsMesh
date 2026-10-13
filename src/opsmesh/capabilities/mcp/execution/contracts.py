from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from opsmesh.capabilities.mcp.execution.diagnostics import (
    MCPErrorDiagnostic,
    public_error,
    public_response,
)
from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload


class MCPToolCallLogRequest(BaseModel):
    mcp_server_id: UUID | None = None
    agent_run_id: UUID | None = None
    approval_id: UUID | None = None
    tool_name: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=32)
    request: dict[str, object] = Field(default_factory=dict)
    response: dict[str, object] | None = None
    error: dict[str, object] | None = None


class MCPToolCallLogResponse(FromAttributesContract):
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
    request_id: str | None
    worker_id: str | None
    runtime_id: str | None
    trace_id: str | None
    span_id: str | None
    request: dict[str, object]
    response: dict[str, object] | None
    error: dict[str, object] | None
    tool_call_id: str | None
    created_at: datetime

    @field_serializer("request")
    def _serialize_request(self, request: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(request)

    @field_serializer("response")
    def _serialize_response(
        self,
        response: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(public_response(response)) if response is not None else None

    @field_serializer("error")
    def _serialize_error(self, error: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(public_error(error)) if error is not None else None


class MCPToolCallDiagnosticResponse(MCPToolCallLogResponse):
    @field_serializer("response")
    def _serialize_response(self, response: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(response) if response is not None else None

    @field_serializer("error")
    def _serialize_error(self, error: dict[str, object] | None) -> dict[str, object] | None:
        return redact_sensitive_payload(error) if error is not None else None


class MCPDiagnosticNotificationResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    agent_run_id: UUID
    mcp_server_id: UUID
    scope: str
    mcp_tool_call_log_id: UUID | None = None
    diagnostic: MCPErrorDiagnostic | None = None
    diagnostic_expired: bool = False
    created_at: datetime


class MCPExecutionError(Exception):
    def __init__(
        self, message: str, *, code: str = "mcp_execution_failed", diagnostic: object = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class MCPExecutionRequest:
    workspace_id: UUID
    agent_run_id: UUID
    tool_name: str
    arguments: dict[str, object]
    agent_profile_id: UUID | None = None
    capability_catalog_fingerprint: str | None = None
    mcp_server_id: UUID | None = None
    tool_call_id: str | None = None


def snapshot_audit_metadata(snapshot: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    version = snapshot.get("version")
    if isinstance(version, int):
        metadata["authorization_snapshot_version"] = version
    for key in (
        "workspace_id",
        "task_id",
        "task_step_id",
        "agent_profile_id",
        "runtime_space_id",
    ):
        value = snapshot.get(key)
        if value is None or isinstance(value, str):
            metadata[f"snapshot_{key}"] = value
    installed_skills = snapshot.get("installed_skills")
    if isinstance(installed_skills, list):
        metadata["snapshot_installed_skills"] = [
            {
                "install_id": item.get("install_id"),
                "installed_key": item.get("installed_key"),
                "installed_version": item.get("installed_version"),
                "source_checksum": item.get("source_checksum"),
                "source_visibility": item.get("source_visibility"),
            }
            for item in installed_skills
            if isinstance(item, dict)
        ]
    raw_tools = snapshot.get("allowed_tools")
    if isinstance(raw_tools, list):
        metadata["snapshot_allowed_tools"] = [tool for tool in raw_tools if isinstance(tool, str)]
    return metadata
