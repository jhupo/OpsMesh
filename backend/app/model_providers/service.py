from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.secrets.service import SecretEncryptionService


@dataclass(frozen=True)
class ResolvedModelProvider:
    model: str
    base_url: str | None
    api_key: str | None
    credential_id: UUID | None


class ModelProviderCredentialService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService,
    ) -> None:
        self._session = session
        self._secret_service = secret_service

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
    ) -> ModelProviderCredential:
        encrypted = self._secret_service.encrypt_payload({"api_key": api_key})
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
        )
        self._session.add(credential)
        self._session.flush()
        if is_default:
            self._unset_other_defaults(workspace_id, credential.id)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=created_by_user_id,
            action="model_provider_credential.created",
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata={
                "name": credential.name,
                "provider": credential.provider,
                "base_url": credential.base_url,
                "default_model": credential.default_model,
                "is_default": credential.is_default,
            },
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def list(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[ModelProviderCredential], int]:
        from sqlalchemy import func

        statement = (
            select(ModelProviderCredential)
            .where(ModelProviderCredential.workspace_id == workspace_id)
            .order_by(ModelProviderCredential.created_at.desc())
        )
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

    def list_usage_audit(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        action: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        from sqlalchemy import func

        allowed_actions = {
            "model_provider.used",
            "model_provider.fallback_unavailable",
        }
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.action.in_(allowed_actions),
        )
        if action is not None:
            if action not in allowed_actions:
                return [], 0
            statement = statement.where(AuditEvent.action == action)
        statement = statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

    def get(
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
        is_default: bool | None = None,
    ) -> ModelProviderCredential:
        credential = self._require(workspace_id=workspace_id, credential_id=credential_id)
        if name is not None:
            credential.name = name
        if provider is not None:
            credential.provider = provider
        if default_model is not None:
            credential.default_model = default_model
        if base_url is not None:
            credential.base_url = base_url
        if is_default is not None:
            credential.is_default = is_default
            if is_default:
                self._unset_other_defaults(workspace_id, credential.id)
        self._audit(
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
        self._audit(
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
        credential.is_default = True
        self._unset_other_defaults(workspace_id, credential.id)
        self._audit(
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
        self._audit(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            credential=credential,
            action="model_provider_credential.disabled",
        )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def record_success(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
    ) -> None:
        if credential_id is None:
            return
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        credential.health_status = "healthy"
        credential.last_success_at = datetime.now(UTC)
        credential.last_failure_code = None
        credential.last_failure_message = None
        self._session.flush([credential])

    def record_failure(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        error_code: str,
        error_message: str,
    ) -> None:
        if credential_id is None:
            return
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            return
        credential.health_status = "degraded"
        credential.last_failure_at = datetime.now(UTC)
        credential.last_failure_code = error_code[:120]
        credential.last_failure_message = error_message[:1000]
        self._session.flush([credential])

    def resolve_for_agent(
        self,
        *,
        workspace_id: UUID,
        agent_credential_id: UUID | None,
        agent_model: str,
    ) -> ResolvedModelProvider:
        credential = None
        if agent_credential_id is not None:
            credential = self.get(workspace_id=workspace_id, credential_id=agent_credential_id)
            if credential is None:
                raise ValueError("Agent model provider credential not found")
        else:
            credential = self._default_credential(workspace_id)
        if credential is None:
            return ResolvedModelProvider(
                model=agent_model,
                base_url=None,
                api_key=None,
                credential_id=None,
            )
        payload = self._secret_service.decrypt_payload(credential.encrypted_api_key)
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Model provider credential is missing api_key")
        model = agent_model or credential.default_model
        if model == "workspace-default":
            model = credential.default_model
        return ResolvedModelProvider(
            model=model,
            base_url=credential.base_url,
            api_key=api_key,
            credential_id=credential.id,
        )

    def _default_credential(self, workspace_id: UUID) -> ModelProviderCredential | None:
        return self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.is_default.is_(True),
                ModelProviderCredential.status == "active",
            )
        )

    def _require(self, *, workspace_id: UUID, credential_id: UUID) -> ModelProviderCredential:
        credential = self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            raise ValueError("Model provider credential not found")
        return credential

    def _audit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        credential: ModelProviderCredential,
        action: str,
    ) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="model_provider_credential",
            target_id=credential.id,
            metadata={
                "name": credential.name,
                "provider": credential.provider,
                "base_url": credential.base_url,
                "default_model": credential.default_model,
                "is_default": credential.is_default,
                "status": credential.status,
            },
        )

    def _unset_other_defaults(self, workspace_id: UUID, credential_id: UUID) -> None:
        self._session.execute(
            update(ModelProviderCredential)
            .where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id != credential_id,
            )
            .values(is_default=False)
        )
