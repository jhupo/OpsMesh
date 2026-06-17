from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agents.model_provider_summary import agent_model_provider_summary
from backend.app.agents.models import AgentProfile
from backend.app.model_providers.health_summary import (
    model_provider_health_check_schedule_summary,
    model_provider_last_health_check_at,
)
from backend.app.model_providers.metadata import sanitize_budget_metadata
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.teams.operations_console_provider_credentials import (
    _empty_health_check_schedule_payload,
)
from backend.app.teams.operations_console_utils import _uuid_or_none


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
