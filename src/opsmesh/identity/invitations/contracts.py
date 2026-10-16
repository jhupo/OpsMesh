"""Injected contracts for invitation acceptance and mail registration.

Invitations own the invitation fact and its transaction; the account it opens
and the mail that carries its token are assembled by bootstrap, so this module
never imports identity.auth or platform.mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OpenedInvitationAccount:
    """The account and first session one accepted invitation produced."""

    user_id: UUID
    session_id: UUID
    expires_at: datetime


class InvitationAccountOpening(Protocol):
    """Open the invited account and start its first session."""

    def open_account(
        self,
        session: Session,
        *,
        email: str,
        display_name: str,
        password: str,
    ) -> OpenedInvitationAccount: ...


class InvitationMailSender(Protocol):
    """Register one rendered invitation mail inside the caller's transaction."""

    def send_account_invitation(
        self,
        session: Session,
        *,
        invitation_id: UUID,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> None: ...
