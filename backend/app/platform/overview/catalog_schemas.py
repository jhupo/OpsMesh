from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    field_serializer,
)

from backend.app.platform.overview.catalog import AdminCatalogKind
from backend.app.shared.security.redaction import redact_sensitive_payload


class AdminCatalogResourceResponse(BaseModel):
    resource_kind: AdminCatalogKind
    resource_id: UUID
    workspace_id: UUID | None
    name: str | None
    status: str | None
    owner_user_id: UUID | None
    created_at: datetime | None
    updated_at: datetime | None
    metadata: dict[str, object]

    @field_serializer("metadata")
    def _serialize_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)
