"""Space member, role and invitation HTTP contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.workspaces.members.models import (
    DEFAULT_INVITATION_TTL_SECONDS,
    SPACE_INVITATION_ROLES,
)

SpaceRoleName = Literal["owner", "admin", "member", "viewer"]
InvitationRoleName = Literal["admin", "member", "viewer"]

MAX_INVITATION_TTL_SECONDS = 30 * 24 * 60 * 60
MIN_INVITATION_TTL_SECONDS = 60 * 60


class MemberRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: InvitationRoleName


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitee_user_id: UUID
    role: InvitationRoleName
    ttl_seconds: int = Field(
        default=DEFAULT_INVITATION_TTL_SECONDS,
        ge=MIN_INVITATION_TTL_SECONDS,
        le=MAX_INVITATION_TTL_SECONDS,
    )


class SpaceMemberView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    user_id: UUID
    role: str
    created_at: datetime
    updated_at: datetime


class SpaceMemberPage(BaseModel):
    members: list[SpaceMemberView]
    total: int


class SpaceInvitationView(BaseModel):
    invitation_id: UUID
    workspace_id: UUID
    invitee_user_id: UUID
    invited_by_user_id: UUID
    role: str
    state: str
    expires_at: datetime
    created_at: datetime


class SpaceInvitationPage(BaseModel):
    invitations: list[SpaceInvitationView]
    total: int


def validated_invitation_role(role: str) -> str:
    """The one place a stated invitation role is checked before storage."""
    if role not in SPACE_INVITATION_ROLES:
        raise ValueError(f"Unknown space invitation role: {role}")
    return role
