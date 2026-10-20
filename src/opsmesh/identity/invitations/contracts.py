"""Injected account-opening contract for invitation acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OpenedInvitationAccount:
    """The account and first session one accepted invitation produced.

    ``session_token`` is the secret the new account presents; the public
    session id never leaves the server.
    """

    user_id: UUID
    session_token: UUID
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
