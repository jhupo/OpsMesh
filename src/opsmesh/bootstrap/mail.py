"""Assemble the identity invitation mail entry and its source admission.

The invitation check is invitation business; the durable mail registration is
mail business. This assembly point is the only place both owners meet, so the
server - not the caller - verifies that one mail really belongs to one
pending invitation. The same admission contracts are re-checked by the
delivery worker: registration-time admission alone cannot survive a revoked
invitation or a rotated token while a mail waits in the queue.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.invitations import service as invitations
from opsmesh.identity.invitations.models import INVITATION_PENDING
from opsmesh.platform.mail import service as mail
from opsmesh.platform.mail.service import (
    PLATFORM_TEST_SOURCE,
    MailAuthorization,
    PlatformTestAuthorization,
    SourceScope,
)
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.secrets import SecretCipher

INVITATION_MAIL_SOURCE = "identity.invitations"


class InvitationMailAuthorization:
    """Admit one invitation mail for its pending invitation and recipient.

    The mail also carries the invitation's token revision it was rendered
    for: a resend rotates the token, so an older intent is refused even
    though the invitation itself is still pending.
    """

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
        revision = _required_revision(scope.source_revision)
        invitation = invitations.get_invitation(session, invitation_id)
        if invitation.email != scope.recipient:
            raise ForbiddenError("The recipient does not match the invitation")
        if invitation.send_count != revision:
            raise ConflictError(
                "The invitation token was rotated after this mail was rendered",
                code="invitation_rotated",
                details={
                    "rendered_revision": revision,
                    "current_revision": invitation.send_count,
                },
            )
        if invitations.effective_status(invitation) != INVITATION_PENDING:
            raise ConflictError(
                "The invitation is no longer pending",
                details={"status": invitations.effective_status(invitation)},
            )


def _required_revision(source_revision: str | None) -> int:
    """Parse the revision one versioned source must declare."""
    if source_revision is None:
        raise ForbiddenError("An invitation mail requires its token revision")
    try:
        return int(source_revision)
    except ValueError as exc:
        raise ForbiddenError("The invitation mail revision is malformed") from exc


def mail_source_authorizations() -> dict[str, MailAuthorization]:
    """The admission contract of every registered mail source.

    The delivery worker admits a source through the same contract that
    registered it; a source without an entry here is never delivered.
    """
    return {
        INVITATION_MAIL_SOURCE: InvitationMailAuthorization(),
        PLATFORM_TEST_SOURCE: PlatformTestAuthorization(),
    }


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
        # The revision is the invitation's current token version: identity has
        # already created or rotated it in this transaction, so the mail is
        # bound to the token its body actually carries.
        revision = str(invitations.get_invitation(session, invitation_id).send_count)
        mail.register_mail(
            session,
            source=INVITATION_MAIL_SOURCE,
            source_ref=str(invitation_id),
            source_revision=revision,
            recipient=recipient,
            subject=subject,
            body=body,
            idempotency_key=idempotency_key,
            authorization=self._authorization,
            cipher=self._cipher,
            daily_source_limit=self._daily_source_limit,
        )
