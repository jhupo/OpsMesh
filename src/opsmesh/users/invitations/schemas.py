"""Platform account invitation request and response contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# The password shape policy belongs to the account-opening owner; the
# invitation schema consumes only its bounds so an over-long password is a
# validation error instead of a hashing failure.
from opsmesh.users.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from opsmesh.users.invitations.service import (
    MAX_INVITATION_TTL_SECONDS,
    MIN_INVITATION_TTL_SECONDS,
)
from opsmesh.users.models import MAX_DISPLAY_NAME_LENGTH


class InvitationView(BaseModel):
    """One invitation; the presented status derives expiry, never rewrites it."""

    model_config = ConfigDict(extra="forbid")

    invitation_id: UUID
    email: EmailStr
    status: str
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None
    token_revision: int
    token_issued_at: datetime | None
    invited_by_user_id: UUID | None


class InvitationIssueView(InvitationView):
    """The newly issued token is returned only once to its administrator."""

    token: UUID = Field(repr=False)
    acceptance_url: str | None = Field(repr=False)


class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    ttl_seconds: int | None = Field(
        default=None, ge=MIN_INVITATION_TTL_SECONDS, le=MAX_INVITATION_TTL_SECONDS
    )


class AcceptInvitationRequest(BaseModel):
    """The invitation token is the credential that opens the account."""

    model_config = ConfigDict(extra="forbid")

    token: UUID
    display_name: str = Field(min_length=1, max_length=MAX_DISPLAY_NAME_LENGTH)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class AcceptInvitationResponse(BaseModel):
    """The account and first session one accepted invitation produced.

    ``session_token`` is the secret the new account presents as
    ``X-Session-ID``; it is never stored, only its digest.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    session_token: UUID
    expires_at: datetime


class InvitationPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitations: list[InvitationView]
    total: int
