"""Transaction callbacks for fixed flow starts and delivery admission."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser

ChannelAdmission = Callable[[Session], bool]


class ChannelFlowStarter(Protocol):
    """Start a fixed flow revision while the caller owns the current transaction."""

    def __call__(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        configuration_id: UUID,
        revision_id: UUID,
        input: dict[str, object],
        idempotency_key: str,
    ) -> UUID: ...
