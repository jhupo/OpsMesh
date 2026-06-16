from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.model_providers.models import ModelProviderCredential


class ModelProviderDefaultService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def unset_other_defaults(
        self,
        workspace_id: UUID,
        credential_id: UUID | None = None,
    ) -> None:
        statement = update(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id
        )
        if credential_id is not None:
            statement = statement.where(ModelProviderCredential.id != credential_id)
        self._session.execute(statement.values(is_default=False))
