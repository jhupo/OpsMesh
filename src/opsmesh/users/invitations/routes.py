"""Platform account invitation create, accept, resend and revoke APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from opsmesh.platform.audit.contracts import AuditActor
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams
from opsmesh.users.access import require_platform_admin
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession, get_auth_service
from opsmesh.users.auth.service import AuthService
from opsmesh.users.errors import AuthenticationError
from opsmesh.users.invitations import service
from opsmesh.users.invitations.models import INVITATION_STATUSES, AccountInvitation
from opsmesh.users.invitations.schemas import (
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    CreateInvitationRequest,
    InvitationIssueView,
    InvitationPage,
    InvitationView,
)

router = APIRouter(prefix="/users/invitations", tags=["users"])


@dataclass(frozen=True)
class InvitationSettings:
    """The configured invitation lifetime and acceptance base URL."""

    ttl_seconds: int
    base_url: str | None


def get_invitation_settings(request: Request) -> InvitationSettings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise AuthenticationError("Invitation configuration is not assembled")
    return InvitationSettings(
        ttl_seconds=settings.invitation_ttl_seconds,
        base_url=settings.platform_base_url or None,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
InvitationSettingsDep = Annotated[InvitationSettings, Depends(get_invitation_settings)]


def _view(invitation: AccountInvitation) -> InvitationView:
    return InvitationView(
        invitation_id=invitation.id,
        email=invitation.email,
        status=service.effective_status(invitation),
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
        token_revision=invitation.token_revision,
        token_issued_at=invitation.token_issued_at,
        invited_by_user_id=invitation.invited_by_user_id,
    )


def _issued(
    invitation: AccountInvitation, token: UUID, base_url: str | None
) -> InvitationIssueView:
    acceptance_url = None
    if base_url:
        acceptance_url = f"{base_url.rstrip('/')}/accept-invitation?{urlencode({'token': token})}"
    return InvitationIssueView(
        **_view(invitation).model_dump(), token=token, acceptance_url=acceptance_url
    )


@router.post(
    "",
    response_model=InvitationIssueView,
    status_code=status.HTTP_201_CREATED,
    operation_id="users.invitations.create",
)
def create_invitation(
    payload: CreateInvitationRequest,
    session: DbSession,
    user: CurrentUserDep,
    invitation_settings: InvitationSettingsDep,
) -> InvitationIssueView:
    """Issue one pending invitation for the administrator to deliver."""
    require_platform_admin(user)
    with session_transaction(session):
        invitation, token = service.create_invitation(
            session,
            email=payload.email,
            invited_by=AuditActor.from_user(user),
            ttl_seconds=payload.ttl_seconds or invitation_settings.ttl_seconds,
        )
    return _issued(invitation, token, invitation_settings.base_url)


@router.get("", response_model=InvitationPage, operation_id="users.invitations.list")
def list_invitations(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> InvitationPage:
    require_platform_admin(user)
    if status_filter is not None and status_filter not in INVITATION_STATUSES:
        raise InvalidArgumentError("The invitation status filter is unknown")
    page = PageParams(limit=limit, offset=offset)
    invitations, total = service.list_invitations(session, page=page, status=status_filter)
    return InvitationPage(invitations=[_view(row) for row in invitations], total=total)


@router.get(
    "/{invitation_id}", response_model=InvitationView, operation_id="users.invitations.read"
)
def read_invitation(
    invitation_id: UUID, session: DbSession, user: CurrentUserDep
) -> InvitationView:
    require_platform_admin(user)
    return _view(service.get_invitation(session, invitation_id))


@router.post(
    "/{invitation_id}/resend",
    response_model=InvitationIssueView,
    operation_id="users.invitations.resend",
)
def resend_invitation(
    invitation_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    invitation_settings: InvitationSettingsDep,
) -> InvitationIssueView:
    """Issue a replacement token; the previous token stops working."""
    require_platform_admin(user)
    with session_transaction(session):
        invitation, token = service.resend_invitation(
            session,
            invitation_id=invitation_id,
            invited_by=AuditActor.from_user(user),
        )
    return _issued(invitation, token, invitation_settings.base_url)


@router.post(
    "/{invitation_id}/revoke",
    response_model=InvitationView,
    operation_id="users.invitations.revoke",
)
def revoke_invitation(
    invitation_id: UUID, session: DbSession, user: CurrentUserDep
) -> InvitationView:
    require_platform_admin(user)
    with session_transaction(session):
        invitation = service.revoke_invitation(
            session, invitation_id=invitation_id, actor=AuditActor.from_user(user)
        )
    return _view(invitation)


@router.post(
    "/accept",
    response_model=AcceptInvitationResponse,
    operation_id="users.invitations.accept",
)
def accept_invitation(
    payload: AcceptInvitationRequest,
    session: DbSession,
    auth_service: AuthServiceDep,
) -> AcceptInvitationResponse:
    """Accept one invitation with its token; no session exists before acceptance."""
    with session_transaction(session):
        opened = service.accept_invitation(
            session,
            token=payload.token,
            display_name=payload.display_name,
            password=payload.password,
            auth_service=auth_service,
        )
    return AcceptInvitationResponse(
        user_id=opened.user.user_id,
        session_token=opened.token,
        expires_at=opened.expires_at,
    )
