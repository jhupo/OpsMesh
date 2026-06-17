from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.model_providers.audit_payloads import budget_metadata_with_model_api
from backend.app.model_providers.audit_writer import ModelProviderAuditWriter
from backend.app.model_providers.credential_queries import ModelProviderCredentialQueryService
from backend.app.model_providers.defaults import ModelProviderDefaultService
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.provider_keys import canonical_model_provider
from backend.app.model_providers.validation import validated_base_url
from backend.app.secrets.service import SecretEncryptionService
from backend.app.security.egress import (
    MODEL_PROVIDER_BASE_URL_POLICY,
    EgressUrlPolicy,
)


class ModelProviderCredentialCommandService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
        egress_policy: EgressUrlPolicy = MODEL_PROVIDER_BASE_URL_POLICY,
    ) -> None:
        self._session = session
        self._secret_service = secret_service
        self._egress_policy = egress_policy

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by_user_id: UUID,
        name: str,
        provider: str,
        api_key: str,
        default_model: str,
        base_url: str | None,
        is_default: bool,
        model_api: str | None = None,
        budget_metadata: dict[str, object] | None = None,
    ) -> ModelProviderCredential:
        provider = canonical_model_provider(provider)
        base_url = validated_base_url(
            base_url,
            provider=provider,
            egress_policy=self._egress_policy,
        )
        encrypted = self._secret_service.encrypt_payload({"api_key": api_key})
        if is_default:
            ModelProviderDefaultService(self._session).unset_other_defaults(workspace_id)
        credential = ModelProviderCredential(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            name=name,
            provider=provider,
            base_url=base_url,
            default_model=default_model,
            encrypted_api_key=encrypted.ciphertext,
            api_key_fingerprint=encrypted.fingerprint,
            encryption_key_id=encrypted.key_id,
            is_default=is_default,
            status="active",
            budget_metadata=budget_metadata_with_model_api(
                budget_metadata,
                model_api=model_api,
                model_api_provided=model_api is not None,
                provider=provider,
            ),
        )
        self._session.add(credential)
        self._session.flush()
        ModelProviderAuditWriter(self._session).credential_created(
            workspace_id=workspace_id,
            user_id=created_by_user_id,
            credential=credential,
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def update(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
        name: str | None = None,
        provider: str | None = None,
        default_model: str | None = None,
        base_url: str | None = None,
        model_api: str | None = None,
        model_api_provided: bool = False,
        is_default: bool | None = None,
        budget_metadata: dict[str, object] | None = None,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        if name is not None:
            credential.name = name
        next_provider = credential.provider
        if provider is not None:
            next_provider = canonical_model_provider(provider)
        if default_model is not None:
            credential.default_model = default_model
        if provider is not None or base_url is not None:
            credential.base_url = validated_base_url(
                base_url if base_url is not None else credential.base_url,
                provider=next_provider,
                egress_policy=self._egress_policy,
            )
            credential.provider = next_provider
        if is_default is not None:
            if is_default:
                ModelProviderDefaultService(self._session).unset_other_defaults(
                    workspace_id,
                    credential.id,
                )
            credential.is_default = is_default
        if budget_metadata is not None or model_api_provided:
            credential.budget_metadata = budget_metadata_with_model_api(
                budget_metadata if budget_metadata is not None else credential.budget_metadata,
                model_api=model_api,
                model_api_provided=model_api_provided,
                provider=next_provider,
            )
        ModelProviderAuditWriter(self._session).credential_changed(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.updated",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def rotate_key(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
        api_key: str,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        encrypted = self._secret_service.encrypt_payload({"api_key": api_key})
        credential.encrypted_api_key = encrypted.ciphertext
        credential.api_key_fingerprint = encrypted.fingerprint
        credential.encryption_key_id = encrypted.key_id
        ModelProviderAuditWriter(self._session).credential_changed(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.rotated",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def set_default(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        ModelProviderDefaultService(self._session).unset_other_defaults(
            workspace_id,
            credential.id,
        )
        credential.is_default = True
        ModelProviderAuditWriter(self._session).credential_changed(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.default_set",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def disable(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        credential.status = "disabled"
        credential.is_default = False
        ModelProviderAuditWriter(self._session).credential_changed(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.disabled",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def _require(self, *, workspace_id: UUID, credential_id: UUID) -> ModelProviderCredential:
        return ModelProviderCredentialQueryService(
            self._session,
            self._secret_service,
        ).require(workspace_id=workspace_id, credential_id=credential_id)
