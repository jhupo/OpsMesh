from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.agents.providers.capabilities import (
    list_model_capabilities,
    resolve_model_capability,
)
from backend.app.domains.agents.providers.health import (
    model_provider_health_check_schedule_summary,
    model_provider_last_health_check_at,
)
from backend.app.domains.agents.providers.metadata import (
    budget_is_exhausted,
    sanitize_budget_metadata,
)
from backend.app.domains.agents.providers.model_api import (
    default_model_api,
    model_api_for_provider,
    model_api_options_for_provider,
)
from backend.app.domains.agents.providers.models import ModelProviderCredential
from backend.app.domains.agents.providers.policy import (
    credential_is_selectable,
    credential_not_selectable_reasons,
    model_provider_base_url_host,
)
from backend.app.domains.agents.providers.views import agent_model_provider_summary
from backend.app.domains.workspace.teams.operations.views import _uuid_or_none


def _provider_management_suggested_actions(
    agent_bindings: list[dict[str, object]],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for index, binding in enumerate(agent_bindings):
        status = binding.get("readiness_status")
        if status not in {"blocked", "degraded"}:
            continue
        actions.append(
            {
                "source": "provider_management",
                "source_index": index,
                "automation": "team_runtime_control",
                "action": "review_model_provider",
                "priority": 100 if status == "blocked" else 60,
                "reason": f"agent_model_provider_{status}",
                "agent_profile_id": binding.get("agent_profile_id"),
                "team_member_id": binding.get("team_member_id"),
                "credential_id": binding.get("credential_id"),
                "provider": binding.get("provider"),
                "selected_model": binding.get("selected_model"),
                "model_api": binding.get("model_api"),
                "reasons": binding.get("reasons", []),
                "warnings": binding.get("warnings", []),
                "available_credential_ids": binding.get("available_credential_ids", []),
                "task_ids": [],
                "task_step_ids": [],
            }
        )
    return actions

def _model_provider_credential_option_payload(
    credential: ModelProviderCredential,
) -> dict[str, object]:
    session = Session.object_session(credential)
    schedule = (
        model_provider_health_check_schedule_summary(
            session,
            workspace_id=credential.workspace_id,
            credential_id=credential.id,
        )
        if session is not None
        else _empty_health_check_schedule_payload()
    )
    capability = resolve_model_capability(credential.provider, credential.default_model)
    return {
        "id": credential.id,
        "name": credential.name,
        "provider": credential.provider,
        "default_model": credential.default_model,
        "model_options": [
            capability.as_dict()
            for capability in list_model_capabilities(provider=credential.provider)
        ],
        "model_api": model_api_for_provider(
            credential.provider,
            credential.budget_metadata,
        ),
        "model_apis": list(model_api_options_for_provider(credential.provider)),
        "default_model_api": default_model_api(credential.provider),
        "model_capability": capability.as_dict() if capability is not None else None,
        "status": credential.status,
        "health_status": credential.health_status,
        "failure_count": credential.failure_count,
        "budget_exhausted": budget_is_exhausted(credential.budget_metadata),
        "is_default": credential.is_default,
        "base_url_configured": bool(credential.base_url),
        "base_url_host": model_provider_base_url_host(credential.base_url),
        "api_key_fingerprint": credential.api_key_fingerprint,
        "last_health_check_at": model_provider_last_health_check_at(credential),
        "scheduled_health_check": schedule,
        "selectable": _credential_selectable(credential),
        "not_selectable_reasons": _credential_not_selectable_reasons(credential),
        "budget_metadata": sanitize_budget_metadata(credential.budget_metadata),
    }


def _model_api_options_payload(provider: object) -> list[str]:
    if not isinstance(provider, str) or not provider.strip():
        return []
    return list(model_api_options_for_provider(provider))


def _default_model_api_payload(provider: object) -> str | None:
    if not isinstance(provider, str) or not provider.strip():
        return None
    return default_model_api(provider)


def _active_credential_ids(credentials: Iterable[ModelProviderCredential]) -> list[UUID]:
    return [credential.id for credential in credentials if _credential_selectable(credential)]


def _credential_selectable(credential: ModelProviderCredential) -> bool:
    return credential_is_selectable(credential)


def _credential_not_selectable_reasons(
    credential: ModelProviderCredential,
) -> list[str]:
    return credential_not_selectable_reasons(credential)


def _empty_health_check_schedule_payload() -> dict[str, object]:
    return {
        "configured": False,
        "active_count": 0,
        "paused_count": 0,
        "next_run_at": None,
        "jobs": [],
    }

def _agent_model_provider_payload(agent: AgentProfile) -> dict[str, object]:
    session = Session.object_session(agent)
    if session is None:
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
            "base_url_configured": False,
            "base_url_host": None,
            "api_key_fingerprint": None,
            "is_default": None,
            "model_api": None,
            "model_capability": None,
            "credential_status": None,
            "credential_health_status": None,
            "failure_count": 0,
            "budget_exhausted": False,
            "readiness_status": "blocked",
            "reasons": ["model_provider_unavailable"],
            "warnings": [],
            "last_health_check_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_code": None,
            "last_failure_message": None,
            "budget_metadata": {},
            "scheduled_health_check": _empty_health_check_schedule_payload(),
        }

    summary = agent_model_provider_summary(session, agent)
    credential = _summary_model_provider_credential(session, summary)
    if credential is None:
        return {
            **summary,
            "last_health_check_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_code": None,
            "last_failure_message": None,
            "budget_metadata": {},
            "scheduled_health_check": _empty_health_check_schedule_payload(),
        }
    return {
        **summary,
        "last_health_check_at": model_provider_last_health_check_at(credential),
        "last_success_at": credential.last_success_at,
        "last_failure_at": credential.last_failure_at,
        "last_failure_code": credential.last_failure_code,
        "last_failure_message": credential.last_failure_message,
        "budget_metadata": sanitize_budget_metadata(credential.budget_metadata),
        "scheduled_health_check": model_provider_health_check_schedule_summary(
            session,
            workspace_id=credential.workspace_id,
            credential_id=credential.id,
        ),
    }


def _summary_model_provider_credential(
    session: Session,
    summary: dict[str, object],
) -> ModelProviderCredential | None:
    credential_id = _uuid_or_none(summary.get("credential_id"))
    if credential_id is None:
        return None
    return session.get(ModelProviderCredential, credential_id)
