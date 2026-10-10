from datetime import datetime
from uuid import UUID

from pydantic import (
    field_serializer,
)

from opsmesh.shared.contracts import ORMModel
from opsmesh.shared.security.redaction import redact_sensitive_payload


class AdminSystemLogResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    actor_type: str
    actor_id: str
    user_id: UUID | None
    agent_run_id: UUID | None
    action: str
    target_type: str
    target_id: str
    request_id: str | None
    trace_id: str | None
    span_id: str | None
    worker_id: str | None
    runtime_id: str | None
    audit_metadata: dict[str, object]
    previous_hash: str | None
    current_hash: str | None
    created_at: datetime

    @field_serializer("audit_metadata")
    def _serialize_audit_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
