from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    field_serializer,
)

from opsmesh.shared.contracts import ORMModel, TimestampedModel
from opsmesh.shared.security.redaction import redact_sensitive_payload


class AdminPlatformPolicyResponse(TimestampedModel):
    policy_key: str
    status: str
    value: dict[str, object]
    description: str
    updated_by: str | None


class AdminPlatformPolicyEventResponse(ORMModel):
    id: UUID
    platform_policy_id: UUID
    event_type: str
    message: str
    event_metadata: dict[str, object]
    created_at: datetime

    @field_serializer("event_metadata")
    def _serialize_event_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminRiskyExecutionPolicyUpdateRequest(BaseModel):
    value: dict[str, object]
    description: str | None = None
    updated_by: str | None = "platform_admin"


class AdminWorkerControlPolicyUpdateRequest(BaseModel):
    value: dict[str, object]
    description: str | None = None
    updated_by: str | None = "platform_admin"
