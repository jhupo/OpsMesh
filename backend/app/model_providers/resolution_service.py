from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.model_providers.resolver import ModelProviderResolver
from backend.app.model_providers.service_models import ResolvedModelProvider
from backend.app.secrets.service import SecretEncryptionService


class ModelProviderResolutionService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
    ) -> None:
        self._resolver = ModelProviderResolver(session, secret_service)

    def resolve_for_agent(
        self,
        *,
        workspace_id: UUID,
        agent_credential_id: UUID | None,
        agent_model: str,
    ) -> ResolvedModelProvider:
        return self._resolver.resolve_for_agent(
            workspace_id=workspace_id,
            agent_credential_id=agent_credential_id,
            agent_model=agent_model,
        )

    def resolve_for_review(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        review_model: str,
    ) -> ResolvedModelProvider:
        return self._resolver.resolve_for_review(
            workspace_id=workspace_id,
            credential_id=credential_id,
            review_model=review_model,
        )
