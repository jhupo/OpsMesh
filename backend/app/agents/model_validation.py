from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.payloads import uuid_or_none
from backend.app.model_providers.model_api import (
    canonical_model_api,
    default_model_api,
    model_api_for_agent_provider,
    model_api_options_for_provider,
    require_known_model_api,
    require_provider_model_api,
    unsupported_agent_model_api,
)
from backend.app.model_providers.models import ModelProviderCredential


class AgentModelValidator:
    def __init__(self, session: Session) -> None:
        self._session = session

    def validate_model_provider_credential(
        self,
        workspace_id: UUID,
        credential_id: object,
    ) -> None:
        credential_uuid = uuid_or_none(credential_id)
        if credential_uuid is None:
            return
        credential = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_uuid,
            )
        )
        if credential is None:
            raise ValueError("Model provider credential not found")
        if credential.status != "active":
            raise ValueError("Model provider credential is not active")

    def validate_agent_model_api(
        self,
        workspace_id: UUID,
        credential_id: object,
        model_settings: object,
    ) -> None:
        if not isinstance(model_settings, Mapping):
            return
        model_api = model_settings.get("model_api")
        if model_api is None:
            return
        credential_uuid = uuid_or_none(credential_id)
        if credential_uuid is None:
            require_known_model_api(model_api)
            return
        credential = self._session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_uuid,
            )
        )
        if credential is None:
            return
        require_provider_model_api(credential.provider, model_api)

    def audit_summary(
        self,
        workspace_id: UUID,
        credential_id: UUID | None,
        model_settings: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return model_provider_audit_summary(
            self._session,
            workspace_id,
            credential_id,
            model_settings,
        )


def model_provider_audit_summary(
    session: Session,
    workspace_id: UUID,
    credential_id: UUID | None,
    model_settings: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if credential_id is None:
        agent_model_api = configured_model_api(model_settings)
        return {
            "credential_id": None,
            "provider": None,
            "default_model": None,
            "model_api": agent_model_api,
            "model_apis": [],
            "default_model_api": None,
            "credential_status": None,
            "credential_health_status": None,
        }
    credential = session.scalar(
        select(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id,
            ModelProviderCredential.id == credential_id,
        )
    )
    if credential is None:
        return {
            "credential_id": str(credential_id),
            "provider": None,
            "default_model": None,
            "model_api": None,
            "model_apis": [],
            "default_model_api": None,
            "credential_status": "missing",
            "credential_health_status": None,
        }
    unsupported_model_api = unsupported_agent_model_api(
        credential.provider,
        dict(model_settings or {}),
    )
    try:
        effective_model_api = model_api_for_agent_provider(
            credential.provider,
            dict(model_settings or {}),
            credential.budget_metadata,
        )
    except ValueError:
        effective_model_api = None
    payload = {
        "credential_id": str(credential.id),
        "provider": credential.provider,
        "default_model": credential.default_model,
        "model_api": effective_model_api,
        "model_apis": list(model_api_options_for_provider(credential.provider)),
        "default_model_api": default_model_api(credential.provider),
        "credential_status": credential.status,
        "credential_health_status": credential.health_status,
        "is_default": credential.is_default,
    }
    if unsupported_model_api is not None:
        payload["requested_model_api"] = unsupported_model_api
    return payload


def configured_model_api(model_settings: Mapping[str, object] | None) -> str | None:
    if model_settings is None:
        return None
    return canonical_model_api(model_settings.get("model_api"))
