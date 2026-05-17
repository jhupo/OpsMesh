from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import TimestampedModel


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    settings: dict[str, object] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = Field(default=None, max_length=32)
    settings: dict[str, object] | None = None


class WorkspaceResponse(TimestampedModel):
    owner_user_id: UUID
    name: str
    slug: str
    status: str
    settings: dict[str, object]


class WorkspaceMemberResponse(TimestampedModel):
    workspace_id: UUID
    user_id: UUID
    role: str
    status: str
