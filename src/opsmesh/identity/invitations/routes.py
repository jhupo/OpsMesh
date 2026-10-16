"""Platform account invitation create, accept, resend and revoke APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.identity.errors import AuthenticationError
from opsmesh.identity.invitations import service
from opsmesh.identity.invitations.contracts import (
    InvitationAccountOpening,
    InvitationMailSender,
)
from opsmesh.identity.invitations.models import INVITATION_STATUSES, AccountInvitation
from opsmesh.identity.invitations.schemas import (
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    CreateInvitationRequest,
    InvitationPage,
    InvitationView,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, PageParams

router = APIRouter(prefix="/invitations", tags=["identity"])


@dataclass(frozen=True)
class InvitationSettings:
    """The configured invitation lifetime and acceptance base URL."""

    ttl_seconds: int
    base_url: str | None


def get_invitation_mail_sender(request: Request) -> InvitationMailSender:
    sender = getattr(request.app.state, "invitation_mail_sender", None)
    if sender is None:
        raise AuthenticationError("Invitation delivery is not assembled")
    return cast(InvitationMailSender, sender)


def get_invitation_account_opening(request: Request) -> InvitationAccountOpening:
    opening = getattr(request.app.state, "invitation_account_opening", None)
    if opening is None:
        raise AuthenticationError("Invitation acceptance is not assembled")
    return cast(InvitationAccountOpening, opening)


def get_invitation_settings(request: Request) -> InvitationSettings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise AuthenticationError("Invitation configuration is not assembled")
    return InvitationSettings(
        ttl_seconds=settings.invitation_ttl_seconds,
        base_url=settings.platform_base_url or None,
    )


MailSenderDep = Annotated[InvitationMailSender, Depends(get_invitation_mail_sender)]
AccountOpeningDep = Annotated[InvitationAccountOpening, Depends(get_invitation_account_opening)]
InvitationSettingsDep = Annotated[InvitationSettings, Depends(get_invitation_settings)]


def _view(invitation: AccountInvitation) -> InvitationView:
    return InvitationView(
        invitation_id=invitation.id,
        email=invitation.email,
        status=service.effective_status(invitation),
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
        send_count=invitation.send_count,
        last_sent_at=invitation.last_sent_at,
        invited_by_user_id=invitation.invited_by_user_id,
    )


@router.post(
    "",
    response_model=InvitationView,
    status_code=status.HTTP_201_CREATED,
    operation_id="identity.invitations.create",
)
def create_invitation(
    payload: CreateInvitationRequest,
    session: DbSession,
    user: CurrentUserDep,
    sender: MailSenderDep,
    invitation_settings: InvitationSettingsDep,
) -> InvitationView:
    """Create one pending invitation and register its mail in the same transaction."""
    require_platform_admin(user)
    with session_transaction(session):
        invitation, _token = service.create_invitation(
            session,
            email=payload.email,
            invited_by=AuditActor.from_user(user),
            sender=sender,
            ttl_seconds=payload.ttl_seconds or invitation_settings.ttl_seconds,
            platform_base_url=invitation_settings.base_url,
        )
    return _view(invitation)


@router.get("", response_model=InvitationPage, operation_id="identity.invitations.list")
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
    "/{invitation_id}", response_model=InvitationView, operation_id="identity.invitations.read"
)
def read_invitation(
    invitation_id: UUID, session: DbSession, user: CurrentUserDep
) -> InvitationView:
    require_platform_admin(user)
    return _view(service.get_invitation(session, invitation_id))


@router.post(
    "/{invitation_id}/resend",
    response_model=InvitationView,
    operation_id="identity.invitations.resend",
)
def resend_invitation(
    invitation_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    sender: MailSenderDep,
    invitation_settings: InvitationSettingsDep,
) -> InvitationView:
    """Resend with a freshly rotated token; the delivered code stops working."""
    require_platform_admin(user)
    with session_transaction(session):
        invitation = service.resend_invitation(
            session,
            invitation_id=invitation_id,
            invited_by=AuditActor.from_user(user),
            sender=sender,
            platform_base_url=invitation_settings.base_url,
        )
    return _view(invitation)


@router.post(
    "/{invitation_id}/revoke",
    response_model=InvitationView,
    operation_id="identity.invitations.revoke",
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
    operation_id="identity.invitations.accept",
)
def accept_invitation(
    payload: AcceptInvitationRequest,
    session: DbSession,
    opening: AccountOpeningDep,
) -> AcceptInvitationResponse:
    """Accept one invitation with its token; no session exists before acceptance."""
    with session_transaction(session):
        opened = service.accept_invitation(
            session,
            token=payload.token,
            display_name=payload.display_name,
            password=payload.password,
            account_opening=opening,
        )
    return AcceptInvitationResponse(
        user_id=opened.user_id,
        session_id=opened.session_id,
        expires_at=opened.expires_at,
    )
