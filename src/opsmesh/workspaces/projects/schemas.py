"""Project HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workspaces.projects.models import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)


class ProjectView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    workspace_id: UUID
    name: str
    description: str
    status: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    projects: list[ProjectView]
    total: int
