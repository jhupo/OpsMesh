from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.model_providers.metadata import budget_is_exhausted
from backend.app.model_providers.model_api import (
    configured_model_api,
    default_model_api,
    model_api_for_agent_provider,
    unsupported_agent_model_api,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.service import (
    model_provider_health_check_schedule_summary,
    model_provider_last_health_check_at,
)
from backend.app.security.redaction import redact_sensitive_text
from backend.app.teams.models import AgentTeamMember
from backend.app.teams.provider_readiness_constants import (
    BLOCKING_HEALTH_STATUSES,
    DEGRADED_HEALTH_STATUSES,
)
from backend.app.teams.provider_readiness_payloads import (
    _capability_provider,
    _empty_health_check_schedule,
    _model_api_options_payload,
    _model_capability_payload,
    _selected_model,
)


class TeamProviderReadinessMemberBuilder:
    """Build the provider readiness payload for one team member."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build(
        self,
        *,
        member: AgentTeamMember,
        agent: AgentProfile | None,
        credential: ModelProviderCredential | None,
    ) -> dict[str, object]:
        reasons, warnings, schedule = self._diagnostics(
            member=member,
            agent=agent,
            credential=credential,
        )
        selected_model = _selected_model(agent, credential)
        unsupported_model_api = self._unsupported_model_api(agent, credential)
        if unsupported_model_api is not None:
            reasons.append("model_api_override_unsupported")
        effective_model_api = self._effective_model_api(agent, credential)
        runtime_participant = member.status == "active" and member.accepts_tasks is True

        return {
            "team_member_id": member.id,
            "agent_profile_id": member.agent_profile_id,
            "agent_name": agent.name if agent is not None else None,
            "team_role": member.team_role,
            "accepts_tasks": member.accepts_tasks,
            "member_status": member.status,
            "runtime_participant": runtime_participant,
            "runtime_blocking": runtime_participant and bool(reasons),
            "runtime_degraded": runtime_participant and not reasons and bool(warnings),
            "readiness_status": _readiness_status(reasons, warnings),
            "reasons": reasons,
            "warnings": warnings,
            "provider": credential.provider if credential is not None else None,
            "model": selected_model,
            "model_api": effective_model_api,
            "requested_model_api": unsupported_model_api,
            "model_apis": _credential_model_apis(credential),
            "default_model_api": _credential_default_model_api(credential),
            "model_capability": _model_capability_payload(
                _capability_provider(agent, credential),
                selected_model,
            ),
            "credential_id": _credential_id(agent, credential),
            "credential_reference": _credential_reference(agent, credential),
            "credential_status": credential.status if credential is not None else None,
            "credential_health_status": (
                credential.health_status if credential is not None else None
            ),
            "failure_count": credential.failure_count if credential is not None else 0,
            "last_failure_at": (
                credential.last_failure_at if credential is not None else None
            ),
            "last_failure_code": (
                credential.last_failure_code if credential is not None else None
            ),
            "last_failure_message": _last_failure_message(credential),
            "budget_exhausted": (
                budget_is_exhausted(credential.budget_metadata)
                if credential is not None
                else False
            ),
            "last_health_check_at": (
                model_provider_last_health_check_at(credential)
                if credential is not None
                else None
            ),
            "scheduled_health_check": schedule,
        }

    def _diagnostics(
        self,
        *,
        member: AgentTeamMember,
        agent: AgentProfile | None,
        credential: ModelProviderCredential | None,
    ) -> tuple[list[str], list[str], dict[str, object]]:
        reasons: list[str] = []
        warnings: list[str] = []

        if agent is None:
            reasons.append("agent_profile_missing")
        elif agent.status != "active":
            reasons.append("agent_profile_not_active")
        if member.status != "active":
            reasons.append("team_member_not_active")

        if credential is None:
            _append_missing_credential_reason(agent, reasons, warnings)
            return reasons, warnings, _empty_health_check_schedule()

        if credential.status != "active":
            reasons.append("model_provider_not_active")
        if credential.health_status in BLOCKING_HEALTH_STATUSES:
            reasons.append("model_provider_unhealthy")
        elif credential.health_status in DEGRADED_HEALTH_STATUSES:
            warnings.append(f"model_provider_{credential.health_status}")
        if budget_is_exhausted(credential.budget_metadata):
            reasons.append("model_provider_budget_exhausted")

        schedule = model_provider_health_check_schedule_summary(
            self._session,
            workspace_id=credential.workspace_id,
            credential_id=credential.id,
        )
        if not schedule.get("configured"):
            warnings.append("model_provider_health_check_not_scheduled")
        return reasons, warnings, schedule

    def _unsupported_model_api(
        self,
        agent: AgentProfile | None,
        credential: ModelProviderCredential | None,
    ) -> str | None:
        if credential is None or agent is None:
            return None
        return unsupported_agent_model_api(credential.provider, agent.model_settings)

    def _effective_model_api(
        self,
        agent: AgentProfile | None,
        credential: ModelProviderCredential | None,
    ) -> str | None:
        agent_model_api = configured_model_api(agent.model_settings) if agent else None
        if credential is None:
            return agent_model_api
        try:
            return model_api_for_agent_provider(
                credential.provider,
                agent.model_settings if agent is not None else None,
                credential.budget_metadata,
            )
        except ValueError:
            return None


def _append_missing_credential_reason(
    agent: AgentProfile | None,
    reasons: list[str],
    warnings: list[str],
) -> None:
    if agent is not None and agent.model_provider_credential_id is not None:
        reasons.append("model_provider_unavailable")
    elif agent is not None and agent.model == "workspace-default":
        reasons.append("workspace_default_model_provider_missing")
    else:
        warnings.append("model_provider_credential_not_configured")


def _readiness_status(reasons: list[str], warnings: list[str]) -> str:
    if reasons:
        return "blocked"
    if warnings:
        return "degraded"
    return "ready"


def _credential_model_apis(credential: ModelProviderCredential | None) -> list[str]:
    if credential is None:
        return []
    return _model_api_options_payload(credential.provider)


def _credential_default_model_api(
    credential: ModelProviderCredential | None,
) -> str | None:
    if credential is None:
        return None
    return default_model_api(credential.provider)


def _credential_id(
    agent: AgentProfile | None,
    credential: ModelProviderCredential | None,
) -> object:
    if credential is not None:
        return credential.id
    if agent is not None:
        return agent.model_provider_credential_id
    return None


def _credential_reference(
    agent: AgentProfile | None,
    credential: ModelProviderCredential | None,
) -> str | None:
    if credential is not None:
        return f"model_provider_credentials:{credential.id}"
    if agent is not None and agent.model_provider_credential_id is not None:
        return f"model_provider_credentials:{agent.model_provider_credential_id}"
    return None


def _last_failure_message(
    credential: ModelProviderCredential | None,
) -> str | None:
    if credential is None or credential.last_failure_message is None:
        return None
    return redact_sensitive_text(credential.last_failure_message)
