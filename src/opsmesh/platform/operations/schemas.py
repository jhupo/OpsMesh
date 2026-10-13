from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from opsmesh.governance.audit.schemas import AuditIntegrityStatusResponse
from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload


class AdminOperationSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    reason: str = Field(min_length=1, max_length=1000)


class AdminOperationResponse(FromAttributesContract):
    id: UUID
    workspace_id: UUID
    actor_user_id: UUID
    operation: str
    status: str
    parameters: dict[str, object]
    result: dict[str, object]
    error_code: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("parameters", "result")
    def _redact(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class PlatformMetricResponse(FromAttributesContract):
    id: UUID
    sample_slot: int
    created_at: datetime
    values: dict[str, object]


class WorkspaceAuditStatus(AuditIntegrityStatusResponse):
    workspace_id: UUID


class FailureCodeCount(BaseModel):
    code: str
    count: int


class SecurityReasonCount(BaseModel):
    reason: str
    count: int


class PlatformExecutionSummary(BaseModel):
    window_seconds: int
    pending_approvals: int
    waiting_runs: int
    blocked_tasks: int
    top_failure_codes: list[FailureCodeCount]
    top_security_reasons: list[SecurityReasonCount]
