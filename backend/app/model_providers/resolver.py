from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.model_providers.availability import credential_is_selectable
from backend.app.model_providers.model_api import model_api_for_provider
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.service_models import (
    ModelProviderUnavailableError,
    ResolvedModelProvider,
)
from backend.app.secrets.service import SecretEncryptionService


class ModelProviderResolver:
    def __init__(self, session: Session, secret_service: SecretEncryptionService) -> None:
        self._session = session
        self._secret_service = secret_service

    def resolve_for_agent(
        self,
        *,
        workspace_id: UUID,
        agent_credential_id: UUID | None,
        agent_model: str,
    ) -> ResolvedModelProvider:
        if agent_credential_id is not None:
            credential = self._selectable_credential(
                workspace_id=workspace_id,
                credential_id=agent_credential_id,
            )
            if credential is None:
                raise ValueError("Agent model provider credential not found or unavailable")
        else:
            credential = self._default_credential(workspace_id)
        if credential is None:
            raise ModelProviderUnavailableError(
                "No available model provider credential for workspace"
            )
        model = agent_model or credential.default_model
        if model == "workspace-default":
            model = credential.default_model
        return self._resolved_provider(credential, model=model)

    def resolve_for_review(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        review_model: str,
    ) -> ResolvedModelProvider:
        credential = (
            self._selectable_credential(
                workspace_id=workspace_id,
                credential_id=credential_id,
            )
            if credential_id is not None
            else self._default_credential(workspace_id)
        )
        if credential is None:
            raise ModelProviderUnavailableError(
                "No available model provider credential for resource review"
            )
        return self._resolved_provider(credential, model=review_model)

    def get_active(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> ModelProviderCredential | None:
        return self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_id,
                ModelProviderCredential.status == "active",
            )
        )

    def _resolved_provider(
        self,
        credential: ModelProviderCredential,
        *,
        model: str,
    ) -> ResolvedModelProvider:
        payload = self._secret_service.decrypt_payload(credential.encrypted_api_key)
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Model provider credential is missing api_key")
        return ResolvedModelProvider(
            provider=credential.provider,
            model=model,
            base_url=credential.base_url,
            api_key=api_key,
            model_api=model_api_for_provider(
                credential.provider,
                credential.budget_metadata,
            ),
            credential_id=credential.id,
        )

    def _default_credential(self, workspace_id: UUID) -> ModelProviderCredential | None:
        default = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.is_default.is_(True),
                ModelProviderCredential.status == "active",
            )
        )
        if credential_is_selectable(default):
            return default
        return None

    def _selectable_credential(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> ModelProviderCredential | None:
        credential = self.get_active(workspace_id=workspace_id, credential_id=credential_id)
        return credential if credential_is_selectable(credential) else None
