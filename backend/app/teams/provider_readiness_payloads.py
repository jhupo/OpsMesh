from __future__ import annotations

from backend.app.agents.models import AgentProfile
from backend.app.model_providers.capabilities import resolve_model_capability
from backend.app.model_providers.model_api import model_api_options_for_provider
from backend.app.model_providers.models import ModelProviderCredential


def _selected_model(
    agent: AgentProfile | None,
    credential: ModelProviderCredential | None,
) -> str | None:
    if agent is None:
        return None
    if credential is None:
        return agent.model
    if agent.model == "workspace-default":
        return credential.default_model
    return agent.model or credential.default_model


def _model_capability_payload(
    provider: str | None,
    model: str | None,
) -> dict[str, object] | None:
    capability = resolve_model_capability(provider, model)
    return capability.as_dict() if capability is not None else None


def _capability_provider(
    agent: AgentProfile | None,
    credential: ModelProviderCredential | None,
) -> str | None:
    if credential is not None:
        return credential.provider
    if agent is None or agent.model == "workspace-default":
        return None
    return "openai"


def _model_api_options_payload(provider: str | None) -> list[str]:
    if provider is None or not provider.strip():
        return []
    return list(model_api_options_for_provider(provider))


def _empty_health_check_schedule() -> dict[str, object]:
    return {
        "configured": False,
        "active_count": 0,
        "paused_count": 0,
        "next_run_at": None,
        "jobs": [],
    }
