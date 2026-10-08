from datetime import datetime
from uuid import UUID

from pydantic import (
    field_serializer,
)

from backend.app.shared.contracts import ORMModel
from backend.app.shared.security.redaction import redact_sensitive_payload


class AdminSecurityEventResponse(ORMModel):
    id: UUID
    workspace_id: UUID | None
    user_id: UUID | None
    action: str
    outcome: str
    severity: str
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str
    reason: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
