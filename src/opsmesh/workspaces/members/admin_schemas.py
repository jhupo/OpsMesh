from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    model_validator,
)

from opsmesh.shared.contracts import ORMModel


class AdminWorkspaceMemberResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: str
    status: str
    created_at: datetime
    updated_at: datetime


class AdminWorkspaceMemberUpdateRequest(BaseModel):
    role: Literal["owner", "admin", "operator", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def _require_update(self) -> "AdminWorkspaceMemberUpdateRequest":
        if self.role is None and self.status is None:
            raise ValueError("role or status is required")
        return self


class AdminWorkspaceMemberCreateRequest(BaseModel):
    user_id: UUID
    role: Literal["owner", "admin", "operator", "viewer"] = "viewer"
