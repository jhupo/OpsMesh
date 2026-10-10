from __future__ import annotations

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.model_api import model_api_options_for_provider
from opsmesh.agents.providers.models import ModelProviderCredential


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
