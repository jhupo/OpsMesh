from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer

from opsmesh.shared.contracts import FromAttributesContract
from opsmesh.shared.security.redaction import redact_sensitive_payload


class AgentSessionSummaryResponse(FromAttributesContract):
    id: UUID
    workspace_id: UUID
    session_key: str
    scope_type: str
    scope_id: str
    status: str
    agent_profile_id: UUID | None
    agent_team_id: UUID | None
    task_id: UUID | None
    metadata: dict[str, object]
    item_count: int
    latest_item_metadata: dict[str, object] | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("metadata", "latest_item_metadata")
    def _serialize_session_metadata(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return redact_sensitive_payload(value) if value is not None else None


class AgentSessionItemResponse(FromAttributesContract):
    id: int
    item: dict[str, object]
    created_at: datetime
    metadata: dict[str, object]

    @field_serializer("item", "metadata")
    def _serialize_item_payload(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AgentSessionDetailResponse(FromAttributesContract):
    session: AgentSessionSummaryResponse
    items: list[AgentSessionItemResponse]
    item_limit: int
    item_offset: int


class AgentSessionClearResponse(BaseModel):
    deleted_item_count: int
