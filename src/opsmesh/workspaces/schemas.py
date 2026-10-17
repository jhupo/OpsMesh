"""Workspace metadata HTTP contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workspaces.models import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH


@dataclass(frozen=True)
class CapacityDefaults:
    """The configured initial capacity limits of a new workspace.

    The values come from the assembled process settings; the module reads
    them through the application state and never imports the configuration
    module itself.
    """

    concurrency_limit: int | None = None
    environment_limit: int | None = None

    def as_limits(self) -> dict[str, int | None]:
        return {"concurrency": self.concurrency_limit, "environment": self.environment_limit}


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)


class TransferOwnershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_user_id: UUID


class WorkspaceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    name: str
    description: str
    status: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class WorkspacePage(BaseModel):
    workspaces: list[WorkspaceView]
    total: int
