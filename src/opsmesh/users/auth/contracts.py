"""Security recording contract injected at application startup."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session


class SecurityEventRecorder(Protocol):
    """Record a security fact; the governance owner is injected by bootstrap."""

    def record(
        self,
        session: Session,
        *,
        kind: str,
        severity: str,
        summary: str,
        details: dict[str, object],
        user_id: UUID | None,
    ) -> None: ...
