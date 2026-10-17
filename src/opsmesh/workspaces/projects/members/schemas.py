"""Project member, role and invitation HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workspaces.projects.members.models import (
    DEFAULT_INVITATION_TTL_SECONDS,
    PROJECT_ROLES,
)

ProjectRoleName = Literal["admin", "member", "viewer"]

MAX_INVITATION_TTL_SECONDS = 30 * 24 * 60 * 60
MIN_INVITATION_TTL_SECONDS = 60 * 60


class MemberRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ProjectRoleName


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitee_user_id: UUID
    role: ProjectRoleName
    ttl_seconds: int = Field(
        default=DEFAULT_INVITATION_TTL_SECONDS,
        ge=MIN_INVITATION_TTL_SECONDS,
        le=MAX_INVITATION_TTL_SECONDS,
    )


class ProjectMemberView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    workspace_id: UUID
    user_id: UUID
    role: str
    created_at: datetime
    updated_at: datetime


class ProjectMemberPage(BaseModel):
    members: list[ProjectMemberView]
    total: int


class ProjectInvitationView(BaseModel):
    invitation_id: UUID
    workspace_id: UUID
    project_id: UUID
    invitee_user_id: UUID
    invited_by_user_id: UUID
    role: str
    state: str
    expires_at: datetime
    created_at: datetime


class ProjectInvitationPage(BaseModel):
    invitations: list[ProjectInvitationView]
    total: int


def validated_role(role: str) -> str:
    """The one place a stated project role is checked before storage."""
    if role not in PROJECT_ROLES:
        raise ValueError(f"Unknown project role: {role}")
    return role
