from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.agents.providers.contracts import ModelProviderUnavailableError
from opsmesh.agents.providers.model_api import (
    canonical_model_api,
    model_api_options_for_provider,
)
from opsmesh.agents.providers.resolution import ModelProviderResolutionService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.shared.config import Settings
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import uuid_or_none


@dataclass(slots=True)
class RunRequestModelProviderService:
    session: Session
    settings: Settings | None

    def provider_for_run(
        self,
        run: AgentRun,
        *,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if override is not None:
            return override
        snapshot = authorization_snapshot_for_run(run).get("model_provider")
        if not isinstance(snapshot, dict):
            raise ValueError("Authorization snapshot model provider is invalid")
        credential_id = uuid_or_none(snapshot.get("credential_id"))
        selected_model = snapshot.get("selected_model")
        expected_provider = snapshot.get("provider")
        if credential_id is None:
            raise ModelProviderUnavailableError(
                "Authorization snapshot has no model provider credential"
            )
        if not isinstance(selected_model, str) or not selected_model:
            raise ValueError("Authorization snapshot selected model is invalid")
        if not isinstance(expected_provider, str) or not expected_provider:
            raise ValueError("Authorization snapshot model provider is invalid")
        resolved = self.resolve(
            workspace_id=run.workspace_id,
            credential_id=credential_id,
            agent_model=selected_model,
            model_api=canonical_model_api(snapshot.get("model_api")),
            prefer_model_api="model_api" in snapshot,
        )
        if resolved["provider"] != expected_provider:
            raise ValueError("Authorized model provider binding has changed")
        return resolved

    def resolve(
        self,
        *,
        workspace_id: UUID,
        credential_id: UUID | None,
        agent_model: str,
        model_api: str | None = None,
        prefer_model_api: bool = False,
    ) -> dict[str, Any]:
        model_api = canonical_model_api(model_api)
        resolved = ModelProviderResolutionService(
            self.session,
            self.secret_service(),
        ).resolve_for_agent(
            workspace_id=workspace_id,
            agent_credential_id=credential_id,
            agent_model=agent_model,
        )
        return {
            "model": resolved.model,
            "provider": resolved.provider,
            "base_url": resolved.base_url,
            "api_key": resolved.api_key,
            "model_api": effective_resolved_model_api(
                provider=resolved.provider,
                resolved_model_api=resolved.model_api,
                requested_model_api=model_api,
                prefer_requested=prefer_model_api,
            ),
            "model_provider_credential_id": resolved.credential_id,
        }

    def secret_service(self) -> SecretEncryptionService:
        if self.settings is None:
            raise ValueError("Settings are required for secret encryption")
        return SecretEncryptionService(
            secret=self.settings.credential_encryption_secret,
            key_id=self.settings.credential_encryption_key_id,
            previous_secrets=self.settings.credential_encryption_previous_secrets,
        )


def effective_resolved_model_api(
    *,
    provider: str | None,
    resolved_model_api: str | None,
    requested_model_api: str | None,
    prefer_requested: bool,
) -> str | None:
    """Choose a supported explicit API override, otherwise the resolved API."""
    requested = canonical_model_api(requested_model_api)
    if (
        prefer_requested
        and requested is not None
        and requested in model_api_options_for_provider(provider)
    ):
        return requested
    return resolved_model_api or requested
