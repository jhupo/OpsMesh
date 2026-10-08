from uuid import UUID

from pydantic import (
    BaseModel,
)

from backend.app.shared.contracts import TimestampedModel


class AdminQuarantineRuntimeSpaceRequest(BaseModel):
    reason: str = "Quarantined by platform admin"


class AdminQuarantineRuntimeSpaceResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    scope: str
    status: str
    reason: str
