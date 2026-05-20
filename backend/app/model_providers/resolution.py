from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.model_providers.models import ModelProviderCredential


@dataclass(frozen=True)
class ModelProviderResolutionSnapshot:
    source: str
    selected_model: str
    agent_model: str
    credential_id: UUID | None
    credential_name: str | None
    provider: str | None
    default_model: str | None
    base_url_host: str | None
    base_url_configured: bool
    api_key_fingerprint: str | None
    is_default: bool | None

    def as_dict(self) -> dict[str, object]:
        credential_id = str(self.credential_id) if self.credential_id is not None else None
        return {
            "source": self.source,
            "selected_model": self.selected_model,
            "agent_model": self.agent_model,
            "credential_id": credential_id,
            "credential_reference": f"model_provider_credentials:{credential_id}"
            if credential_id is not None
            else None,
            "credential_name": self.credential_name,
            "provider": self.provider,
            "default_model": self.default_model,
            "base_url_host": self.base_url_host,
            "base_url_configured": self.base_url_configured,
            "api_key_fingerprint": self.api_key_fingerprint,
            "is_default": self.is_default,
        }


class ModelProviderResolutionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_snapshot_for_agent(
        self,
        *,
        workspace_id: UUID,
        agent_credential_id: UUID | None,
        agent_model: str,
    ) -> ModelProviderResolutionSnapshot:
        credential = None
        source = "agent_model"
        if agent_credential_id is not None:
            credential = self._active_credential(
                workspace_id=workspace_id,
                credential_id=agent_credential_id,
            )
            if credential is None:
                raise ValueError("Agent model provider credential not found")
            source = "agent_override"
        else:
            credential = self._default_credential(workspace_id)
            if credential is not None:
                source = "workspace_default"

        selected_model = _selected_model(agent_model, credential)
        return ModelProviderResolutionSnapshot(
            source=source,
            selected_model=selected_model,
            agent_model=agent_model,
            credential_id=credential.id if credential is not None else None,
            credential_name=credential.name if credential is not None else None,
            provider=credential.provider if credential is not None else None,
            default_model=credential.default_model if credential is not None else None,
            base_url_host=_base_url_host(credential.base_url) if credential is not None else None,
            base_url_configured=bool(credential and credential.base_url),
            api_key_fingerprint=credential.api_key_fingerprint if credential is not None else None,
            is_default=credential.is_default if credential is not None else None,
        )

    def _active_credential(
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

    def _default_credential(self, workspace_id: UUID) -> ModelProviderCredential | None:
        return self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.is_default.is_(True),
                ModelProviderCredential.status == "active",
            )
        )


def _selected_model(agent_model: str, credential: ModelProviderCredential | None) -> str:
    if credential is None:
        return agent_model
    if not agent_model or agent_model == "workspace-default":
        return credential.default_model
    return agent_model


def _base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    if parsed.netloc:
        return parsed.netloc
    return parsed.path or None
