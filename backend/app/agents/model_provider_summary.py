from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.agents import AgentProfileResponse
from backend.app.model_providers.capabilities import resolve_model_capability
from backend.app.model_providers.metadata import budget_is_exhausted
from backend.app.model_providers.model_api import (
    configured_model_api,
    default_model_api,
    model_api_for_agent_provider,
    model_api_options_for_provider,
    unsupported_agent_model_api,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.resolution import (
    ModelProviderResolutionService,
    ModelProviderResolutionSnapshot,
)


def agent_profile_response(
    db_session: Session,
    agent: AgentProfile,
) -> AgentProfileResponse:
    payload = {
        **AgentProfileResponse.model_validate(agent).model_dump(mode="python"),
        "model_provider": agent_model_provider_summary(db_session, agent),
    }
    return AgentProfileResponse.model_validate(payload)


def agent_model_provider_summary(
    db_session: Session,
    agent: AgentProfile,
) -> dict[str, object]:
    try:
        snapshot = ModelProviderResolutionService(db_session).resolve_snapshot_for_agent(
            workspace_id=agent.workspace_id,
            agent_credential_id=agent.model_provider_credential_id,
            agent_model=agent.model,
        )
    except ValueError:
        credential = (
            db_session.get(ModelProviderCredential, agent.model_provider_credential_id)
            if agent.model_provider_credential_id is not None
            else None
        )
        if credential is not None and credential.workspace_id == agent.workspace_id:
            credential_id = str(credential.id)
            unsupported_model_api = unsupported_agent_model_api(
                credential.provider,
                agent.model_settings,
            )
            payload = {
                "source": "agent_override",
                "selected_model": _selected_model(agent.model, credential),
                "agent_model": agent.model,
                "credential_id": credential_id,
                "credential_reference": f"model_provider_credentials:{credential_id}",
                "credential_name": credential.name,
                "provider": credential.provider,
                "default_model": credential.default_model,
                "base_url_host": _base_url_host(credential.base_url),
                "base_url_configured": bool(credential.base_url),
                "api_key_fingerprint": credential.api_key_fingerprint,
                "is_default": credential.is_default,
                "model_api": model_api_for_agent_provider(
                    credential.provider,
                    agent.model_settings,
                    credential.budget_metadata,
                ),
                "requested_model_api": unsupported_model_api,
                "model_apis": list(model_api_options_for_provider(credential.provider)),
                "default_model_api": default_model_api(credential.provider),
                "model_capability": _model_capability_payload(
                    credential.provider,
                    _selected_model(agent.model, credential),
                ),
            }
            payload.update(_agent_model_provider_health_summary(db_session, credential.id))
            if unsupported_model_api is not None:
                payload["warnings"] = [
                    *list(payload.get("warnings", [])),
                    "model_api_override_unsupported",
                ]
            return payload
        return {
            "source": "unavailable",
            "selected_model": agent.model,
            "agent_model": agent.model,
            "credential_id": str(agent.model_provider_credential_id)
            if agent.model_provider_credential_id is not None
            else None,
            "credential_reference": None,
            "credential_name": None,
            "provider": None,
            "default_model": None,
            "base_url_host": None,
            "base_url_configured": False,
            "api_key_fingerprint": None,
            "is_default": None,
            "model_api": configured_model_api(agent.model_settings),
            "model_apis": [],
            "default_model_api": None,
            "model_capability": _model_capability_payload(None, agent.model),
            "credential_status": None,
            "credential_health_status": None,
            "failure_count": 0,
            "budget_exhausted": False,
            "readiness_status": "blocked",
            "reasons": ["model_provider_unavailable"],
            "warnings": [],
        }
    summary = snapshot.as_dict()
    summary["model_api"] = model_api_for_agent_provider(
        snapshot.provider,
        agent.model_settings,
        {"model_api": summary.get("model_api")},
    ) or summary.get("model_api")
    unsupported_model_api = unsupported_agent_model_api(
        snapshot.provider,
        agent.model_settings,
    )
    summary["requested_model_api"] = unsupported_model_api
    summary["model_capability"] = _model_capability_payload(
        _capability_provider(snapshot),
        snapshot.selected_model,
    )
    summary.update(_agent_model_provider_health_summary(db_session, snapshot))
    if unsupported_model_api is not None:
        summary["warnings"] = [
            *list(summary.get("warnings", [])),
            "model_api_override_unsupported",
        ]
    return summary


def _agent_model_provider_health_summary(
    db_session: Session,
    snapshot_or_credential_id: ModelProviderResolutionSnapshot | object,
) -> dict[str, object]:
    credential_id = (
        snapshot_or_credential_id.credential_id
        if isinstance(snapshot_or_credential_id, ModelProviderResolutionSnapshot)
        else snapshot_or_credential_id
    )
    if credential_id is None:
        return {
            "credential_status": None,
            "credential_health_status": None,
            "failure_count": 0,
            "budget_exhausted": False,
            "readiness_status": "ready",
            "reasons": [],
            "warnings": [],
        }
    credential = db_session.get(ModelProviderCredential, credential_id)
    reasons: list[str] = []
    warnings: list[str] = []
    if credential is None:
        reasons.append("model_provider_unavailable")
        return {
            "credential_status": None,
            "credential_health_status": None,
            "failure_count": 0,
            "budget_exhausted": False,
            "readiness_status": "blocked",
            "reasons": reasons,
            "warnings": warnings,
        }
    if credential.status != "active":
        reasons.append("model_provider_not_active")
    if credential.health_status == "unhealthy":
        reasons.append("model_provider_unhealthy")
    elif credential.health_status in {"degraded", "unknown"}:
        warnings.append(f"model_provider_{credential.health_status}")
    exhausted = budget_is_exhausted(credential.budget_metadata)
    if exhausted:
        reasons.append("model_provider_budget_exhausted")
    return {
        "credential_status": credential.status,
        "credential_health_status": credential.health_status,
        "failure_count": credential.failure_count,
        "budget_exhausted": exhausted,
        "readiness_status": "blocked" if reasons else "degraded" if warnings else "ready",
        "reasons": reasons,
        "warnings": warnings,
    }


def _capability_provider(snapshot: ModelProviderResolutionSnapshot) -> str | None:
    if snapshot.provider is not None:
        return snapshot.provider
    if snapshot.agent_model == "workspace-default":
        return None
    return "openai"


def _selected_model(agent_model: str, credential: ModelProviderCredential) -> str:
    return (
        credential.default_model
        if not agent_model or agent_model == "workspace-default"
        else agent_model
    )


def _base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    return parsed.netloc or parsed.path or None


def _model_capability_payload(
    provider: str | None,
    model: str | None,
) -> dict[str, object] | None:
    capability = resolve_model_capability(provider, model)
    return capability.as_dict() if capability is not None else None
