"""Assemble the identity invitation mail entry and its source admission.

The invitation check is invitation business; the durable mail registration is
mail business. This assembly point is the only place both owners meet, so the
server - not the caller - verifies that one mail really belongs to one
pending invitation.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.invitations import service as invitations
from opsmesh.identity.invitations.models import INVITATION_PENDING
from opsmesh.platform.mail import service as mail
from opsmesh.platform.mail.service import MailAuthorization, SourceScope
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.secrets import SecretCipher

INVITATION_MAIL_SOURCE = "identity.invitations"


class InvitationMailAuthorization:
    """Admit one invitation mail for its pending invitation and recipient."""

    def authorize(self, session: Session, scope: SourceScope) -> None:
        if scope.source != INVITATION_MAIL_SOURCE:
            raise ForbiddenError(
                "The mail source is not registered", details={"source": scope.source}
            )
        if scope.source_ref is None:
            raise ForbiddenError("An invitation mail requires its invitation reference")
        try:
            invitation_id = UUID(scope.source_ref)
        except ValueError as exc:
            raise ForbiddenError("The invitation reference is malformed") from exc
        invitation = invitations.get_invitation(session, invitation_id)
        if invitation.email != scope.recipient:
            raise ForbiddenError("The recipient does not match the invitation")
        if invitations.effective_status(invitation) != INVITATION_PENDING:
            raise ConflictError(
                "The invitation is no longer pending",
                details={"status": invitations.effective_status(invitation)},
            )


class InvitationMailRegistrar:
    """The identity entry point: register one rendered invitation mail."""

    def __init__(
        self,
        *,
        authorization: MailAuthorization,
        cipher: SecretCipher,
        daily_source_limit: int,
    ) -> None:
        self._authorization = authorization
        self._cipher = cipher
        self._daily_source_limit = daily_source_limit

    def send_account_invitation(
        self,
        session: Session,
        *,
        invitation_id: UUID,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> None:
        mail.register_mail(
            session,
            source=INVITATION_MAIL_SOURCE,
            source_ref=str(invitation_id),
            recipient=recipient,
            subject=subject,
            body=body,
            idempotency_key=idempotency_key,
            authorization=self._authorization,
            cipher=self._cipher,
            daily_source_limit=self._daily_source_limit,
        )
