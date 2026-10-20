"""Model selection authorization supplied by application assembly."""

from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session


@runtime_checkable
class ModelSelectionAuthorization(Protocol):
    def authorize_model(
        self,
        session: Session,
        *,
        user_id: UUID,
        connection_id: UUID,
        model: str,
    ) -> int:
        """Return the authorized connection revision, without decrypting its API key."""
        ...
