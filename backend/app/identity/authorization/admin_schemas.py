from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
)

from backend.app.identity.authorization.resources import ResourceAction, ResourceKind


class AdminResourceOwnerUpdateRequest(BaseModel):
    user_id: UUID


class AdminResourceGrantUpdateRequest(BaseModel):
    user_id: UUID
    actions: frozenset[ResourceAction] = Field(default_factory=frozenset)


class AdminResourceGrantResponse(BaseModel):
    user_id: UUID
    actions: list[ResourceAction]


class AdminResourceAuthorizationResponse(BaseModel):
    workspace_id: UUID
    resource_kind: ResourceKind
    resource_id: UUID
    owner_user_id: UUID | None
    grants: list[AdminResourceGrantResponse]
