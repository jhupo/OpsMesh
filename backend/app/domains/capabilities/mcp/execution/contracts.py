from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from backend.app.core.contracts import ORMModel
from backend.app.core.security.redaction import redact_sensitive_payload


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
    trace_id: str | None
    span_id: str | None
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


class McpExecutionError(Exception):
    def __init__(self, message: str, *, code: str = "mcp_execution_failed") -> None:
        super().__init__(message)
        self.code = code


class McpExecutionPending(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        response: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.response = response


@dataclass(frozen=True)
class McpExecutionRequest:
    workspace_id: UUID
    agent_run_id: UUID
    tool_name: str
    arguments: dict[str, object]
    mcp_server_id: UUID | None = None
    runtime_allowed_tools: tuple[str, ...] | None = None
    approval_granted: bool = False


@dataclass(frozen=True)
class McpExecutionResult:
    status: str
    response: dict[str, object] | None
    error: dict[str, object] | None
    log_id: UUID
    latency_ms: int


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
