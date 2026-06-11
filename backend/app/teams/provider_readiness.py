from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
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
from backend.app.model_providers.service import (
    model_provider_health_check_schedule_summary,
    model_provider_last_health_check_at,
)
from backend.app.security.redaction import redact_sensitive_text
from backend.app.teams.models import AgentTeamMember

BLOCKING_HEALTH_STATUSES = {"unhealthy"}
DEGRADED_HEALTH_STATUSES = {"degraded", "unknown"}


class TeamProviderReadinessService:
    """Summarize whether team members have usable model providers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_readiness(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
    ) -> dict[str, object]:
        members = list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )
        items = [self._member_readiness(member) for member in members]
        blocking_items = [item for item in items if item["readiness_status"] == "blocked"]
        degraded_items = [item for item in items if item["readiness_status"] == "degraded"]
        runtime_items = [item for item in items if item["runtime_participant"] is True]
        runtime_blocking_items = [item for item in items if item["runtime_blocking"] is True]
        runtime_degraded_items = [item for item in items if item["runtime_degraded"] is True]
        health = (
            "blocked"
            if runtime_blocking_items
            else "degraded"
            if blocking_items or degraded_items
            else "healthy"
        )
        return {
            "status": health,
            "member_count": len(items),
            "runtime_participant_count": len(runtime_items),
            "ready_member_count": sum(
                1 for item in items if item["readiness_status"] == "ready"
            ),
            "degraded_member_count": len(degraded_items),
            "blocked_member_count": len(blocking_items),
            "runtime_ready_member_count": sum(
                1 for item in runtime_items if item["readiness_status"] == "ready"
            ),
            "runtime_degraded_member_count": len(runtime_degraded_items),
            "runtime_blocked_member_count": len(runtime_blocking_items),
            "requires_operator_attention": bool(blocking_items or degraded_items),
            "blocking_reasons": _reason_counts(blocking_items),
            "warning_reasons": _reason_counts(degraded_items),
            "runtime_blocking_reasons": _reason_counts(runtime_blocking_items),
            "runtime_warning_reasons": _reason_counts(runtime_degraded_items),
            "members": items,
            "action_plan": _action_plan(
                runtime_blocking_items,
                [
                    item
                    for item in [*blocking_items, *degraded_items]
                    if item not in runtime_blocking_items
                ],
            ),
        }

    def _member_readiness(self, member: AgentTeamMember) -> dict[str, object]:
        agent = self._session.get(AgentProfile, member.agent_profile_id)
        credential = _agent_model_provider_credential(self._session, agent)
        reasons: list[str] = []
        warnings: list[str] = []

        if agent is None:
            reasons.append("agent_profile_missing")
        elif agent.status != "active":
            reasons.append("agent_profile_not_active")
        if member.status != "active":
            reasons.append("team_member_not_active")

        if credential is None:
            if agent is not None and agent.model_provider_credential_id is not None:
                reasons.append("model_provider_unavailable")
            elif agent is not None and agent.model == "workspace-default":
                reasons.append("workspace_default_model_provider_missing")
            else:
                warnings.append("model_provider_credential_not_configured")
        else:
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
        readiness_status = (
            "blocked" if reasons else "degraded" if warnings else "ready"
        )
        selected_model = _selected_model(agent, credential)
        agent_model_api = (
            configured_model_api(agent.model_settings) if agent is not None else None
        )
        unsupported_model_api = (
            unsupported_agent_model_api(credential.provider, agent.model_settings)
            if credential is not None and agent is not None
            else None
        )
        if unsupported_model_api is not None:
            reasons.append("model_api_override_unsupported")
        effective_model_api = agent_model_api
        if credential is not None:
            try:
                effective_model_api = model_api_for_agent_provider(
                    credential.provider,
                    agent.model_settings if agent is not None else None,
                    credential.budget_metadata,
                )
            except ValueError:
                effective_model_api = None
        readiness_status = (
            "blocked" if reasons else "degraded" if warnings else "ready"
        )
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
            "readiness_status": readiness_status,
            "reasons": reasons,
            "warnings": warnings,
            "provider": credential.provider if credential is not None else None,
            "model": selected_model,
            "model_api": effective_model_api,
            "requested_model_api": unsupported_model_api,
            "model_apis": _model_api_options_payload(credential.provider)
            if credential is not None
            else [],
            "default_model_api": default_model_api(credential.provider)
            if credential is not None
            else None,
            "model_capability": _model_capability_payload(
                _capability_provider(agent, credential),
                selected_model,
            ),
            "credential_id": (
                credential.id
                if credential is not None
                else agent.model_provider_credential_id
                if agent is not None
                else None
            ),
            "credential_reference": (
                f"model_provider_credentials:{credential.id}"
                if credential is not None
                else f"model_provider_credentials:{agent.model_provider_credential_id}"
                if agent is not None and agent.model_provider_credential_id is not None
                else None
            ),
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
            "last_failure_message": (
                redact_sensitive_text(credential.last_failure_message)
                if credential is not None and credential.last_failure_message is not None
                else None
            ),
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
            "scheduled_health_check": (
                schedule if credential is not None else _empty_health_check_schedule()
            ),
        }


def _agent_model_provider_credential(
    session: Session,
    agent: AgentProfile | None,
) -> ModelProviderCredential | None:
    if agent is None:
        return None
    if agent.model_provider_credential_id is not None:
        credential = session.scalar(
            select(ModelProviderCredential).where(
                ModelProviderCredential.workspace_id == agent.workspace_id,
                ModelProviderCredential.id == agent.model_provider_credential_id,
            )
        )
        return credential
    return _workspace_default_credential(session, agent.workspace_id)


def _workspace_default_credential(
    session: Session,
    workspace_id: UUID,
) -> ModelProviderCredential | None:
    return session.scalar(
        select(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id,
            ModelProviderCredential.is_default.is_(True),
            ModelProviderCredential.status == "active",
        )
    )


def _credential_selectable(credential: ModelProviderCredential | None) -> bool:
    if credential is None:
        return False
    if credential.status != "active":
        return False
    if credential.health_status in BLOCKING_HEALTH_STATUSES:
        return False
    return not budget_is_exhausted(credential.budget_metadata)


def _selected_model(
    agent: AgentProfile | None,
    credential: ModelProviderCredential | None,
) -> str | None:
    if agent is None:
        return None
    if credential is None:
        return agent.model
    return (
        credential.default_model
        if agent.model == "workspace-default"
        else agent.model or credential.default_model
    )


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


def _reason_counts(items: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        values = item.get("reasons")
        if not values:
            values = item.get("warnings")
        if not isinstance(values, list):
            continue
        for reason in values:
            if isinstance(reason, str):
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _action_plan(
    blocking_items: list[dict[str, object]],
    degraded_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    action_plan: list[dict[str, object]] = []
    for index, item in enumerate(blocking_items):
        action_plan.append(
            {
                "source": "provider_readiness",
                "source_index": index,
                "automation": "team_runtime_control",
                "action": "review_model_provider",
                "priority": 100,
                "reason": "team_model_provider_blocked",
                "agent_profile_id": item["agent_profile_id"],
                "team_member_id": item["team_member_id"],
                "credential_id": item["credential_id"],
                "credential_reference": item["credential_reference"],
                "provider": item["provider"],
                "model": item["model"],
                "model_api": item["model_api"],
                "model_apis": item["model_apis"],
                "default_model_api": item["default_model_api"],
                "reasons": item["reasons"],
                "failure_count": item.get("failure_count", 0),
                "last_failure_at": item.get("last_failure_at"),
                "last_failure_code": item.get("last_failure_code"),
                "last_failure_message": item.get("last_failure_message"),
                "task_ids": [],
                "task_step_ids": [],
            }
        )
    offset = len(action_plan)
    for index, item in enumerate(degraded_items[:5]):
        action_plan.append(
            {
                "source": "provider_readiness",
                "source_index": offset + index,
                "automation": "team_runtime_control",
                "action": "review_model_provider",
                "priority": 60,
                "reason": "team_model_provider_degraded",
                "agent_profile_id": item["agent_profile_id"],
                "team_member_id": item["team_member_id"],
                "credential_id": item["credential_id"],
                "credential_reference": item["credential_reference"],
                "provider": item["provider"],
                "model": item["model"],
                "model_api": item["model_api"],
                "model_apis": item["model_apis"],
                "default_model_api": item["default_model_api"],
                "warnings": item["warnings"],
                "reasons": item["reasons"],
                "failure_count": item.get("failure_count", 0),
                "last_failure_at": item.get("last_failure_at"),
                "last_failure_code": item.get("last_failure_code"),
                "last_failure_message": item.get("last_failure_message"),
                "task_ids": [],
                "task_step_ids": [],
            }
        )
    return action_plan


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
