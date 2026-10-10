from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
)

from opsmesh.capabilities.governance.admin_service import AdminCapabilityKind


class AdminCapabilityGovernanceRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class AdminCapabilityGovernanceResponse(BaseModel):
    kind: AdminCapabilityKind
    resource_id: UUID
    workspace_id: UUID | None
    status: str
    platform_blocked: bool
    platform_previous_status: str | None
