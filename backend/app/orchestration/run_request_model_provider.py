from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.core.config import Settings
from backend.app.model_providers.model_api import canonical_model_api
from backend.app.model_providers.resolution_service import ModelProviderResolutionService
from backend.app.runs.models import AgentRun
from backend.app.secrets.service import SecretEncryptionService

from .run_request_authorization import RunAuthorizationService
from .run_request_utils import effective_resolved_model_api, model_api_from_settings, uuid_or_none


@dataclass(slots=True)
class RunRequestModelProviderService:
    session: Session
    settings: Settings | None

    def provider_for_run(
        self,
        run: AgentRun,
        profile: AgentProfile,
        *,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if override is not None:
            return override
        snapshot = RunAuthorizationService(self.session).authorization_snapshot_for_run(run).get(
            "model_provider"
        )
        credential_id = profile.model_provider_credential_id
        agent_model = profile.model
        prefer_model_api = False
        if isinstance(snapshot, dict):
            source = snapshot.get("source")
            if source == "agent_override":
                credential_id = uuid_or_none(snapshot.get("credential_id"))
                selected_model = snapshot.get("selected_model")
                if isinstance(selected_model, str) and selected_model:
                    agent_model = selected_model
            model_api = canonical_model_api(snapshot.get("model_api"))
            prefer_model_api = "model_api" in snapshot
        else:
            model_api = model_api_from_settings(profile.model_settings)
            prefer_model_api = model_api is not None
        return self.resolve(
            workspace_id=run.workspace_id,
            credential_id=credential_id,
            agent_model=agent_model,
            model_api=model_api,
            prefer_model_api=prefer_model_api,
        )

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
