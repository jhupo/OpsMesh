from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeamMember
from backend.app.teams.operations_console_provider_actions import (
    _provider_management_suggested_actions,
)
from backend.app.teams.operations_console_provider_credentials import (
    _active_credential_ids,
    _default_model_api_payload,
    _model_api_options_payload,
    _model_provider_credential_option_payload,
)
from backend.app.teams.operations_console_provider_runs import (
    _run_model_provider_snapshot,
)
from backend.app.teams.operations_console_provider_summary import (
    _agent_model_provider_payload,
)
from backend.app.teams.operations_console_utils import (
    _string_list,
    _uuid_or_none,
)

PROVIDER_RUN_STATUSES = {
    RunStatus.QUEUED.value,
    RunStatus.RUNNING.value,
    RunStatus.WAITING_RUNTIME.value,
    RunStatus.WAITING_APPROVAL.value,
}


class TeamProviderManagementBuilder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def payload(
        self,
        *,
        workspace_id: UUID,
        members: list[AgentTeamMember],
    ) -> dict[str, object]:
        credentials = list(
            self._session.scalars(
                select(ModelProviderCredential)
                .where(ModelProviderCredential.workspace_id == workspace_id)
                .order_by(
                    ModelProviderCredential.is_default.desc(),
                    ModelProviderCredential.status.asc(),
                    ModelProviderCredential.name.asc(),
                    ModelProviderCredential.id.asc(),
                )
            )
        )
        credential_by_id = {credential.id: credential for credential in credentials}
        agent_bindings = [
            self._agent_provider_binding_payload(
                member=member,
                credential_by_id=credential_by_id,
            )
            for member in members
        ]
        return {
            "credential_count": len(credentials),
            "active_credential_count": sum(
                1 for credential in credentials if credential.status == "active"
            ),
            "default_credential_id": next(
                (
                    credential.id
                    for credential in credentials
                    if credential.is_default and credential.status == "active"
                ),
                None,
            ),
            "credentials": [
                _model_provider_credential_option_payload(credential)
                for credential in credentials
            ],
            "agent_bindings": agent_bindings,
            "run_diagnostics": self._provider_run_diagnostics_payload(
                workspace_id=workspace_id,
                members=members,
            ),
            "suggested_actions": _provider_management_suggested_actions(agent_bindings),
        }


    def _agent_provider_binding_payload(
        self,
        *,
        member: AgentTeamMember,
        credential_by_id: dict[UUID, ModelProviderCredential],
    ) -> dict[str, object]:
        agent = self._session.get(AgentProfile, member.agent_profile_id)
        if agent is None:
            return {
                "team_member_id": member.id,
                "agent_profile_id": member.agent_profile_id,
                "agent_name": None,
                "team_role": member.team_role,
                "accepts_tasks": member.accepts_tasks,
                "agent_model": None,
                "selected_model": None,
                "source": "unavailable",
                "credential_id": None,
                "credential_name": None,
                "provider": None,
                "readiness_status": "blocked",
                "reasons": ["agent_profile_missing"],
                "warnings": [],
                "available_credential_ids": _active_credential_ids(credential_by_id.values()),
            }
        summary = _agent_model_provider_payload(agent)
        selected_credential_id = _uuid_or_none(summary.get("credential_id"))
        return {
            "team_member_id": member.id,
            "agent_profile_id": agent.id,
            "agent_name": agent.name,
            "team_role": member.team_role,
            "accepts_tasks": member.accepts_tasks,
            "agent_model": agent.model,
            "selected_model": summary.get("selected_model"),
            "source": summary.get("source"),
            "credential_id": selected_credential_id,
            "credential_name": summary.get("credential_name"),
            "credential_reference": summary.get("credential_reference"),
            "provider": summary.get("provider"),
            "default_model": summary.get("default_model"),
            "model_api": summary.get("model_api"),
            "requested_model_api": summary.get("requested_model_api"),
            "model_apis": _model_api_options_payload(summary.get("provider")),
            "default_model_api": _default_model_api_payload(summary.get("provider")),
            "model_capability": summary.get("model_capability"),
            "credential_status": summary.get("credential_status"),
            "credential_health_status": summary.get("credential_health_status"),
            "budget_exhausted": summary.get("budget_exhausted"),
            "readiness_status": summary.get("readiness_status"),
            "reasons": _string_list(summary.get("reasons")),
            "warnings": _string_list(summary.get("warnings")),
            "available_credential_ids": _active_credential_ids(credential_by_id.values()),
        }


    def _provider_run_diagnostics_payload(
        self,
        *,
        workspace_id: UUID,
        members: list[AgentTeamMember],
        limit: int = 20,
    ) -> dict[str, object]:
        if not members:
            return {"total": 0, "items": [], "truncated": False}
        team_id = members[0].agent_team_id
        runs = list(
            self._session.scalars(
                select(AgentRun)
                .join(Task, Task.id == AgentRun.task_id)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.status.in_(PROVIDER_RUN_STATUSES),
                    Task.workspace_id == workspace_id,
                    Task.agent_team_id == team_id,
                )
                .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
                .limit(limit + 1)
            )
        )
        items = [
            self._provider_run_payload(run)
            for run in runs[:limit]
        ]
        return redact_sensitive_payload(
            {
                "total": len(runs),
                "items": items,
                "truncated": len(runs) > limit,
                "statuses": sorted(
                    {
                        str(item.get("status"))
                        for item in items
                        if isinstance(item.get("status"), str)
                    }
                ),
            }
        )


    def _provider_run_payload(self, run: AgentRun) -> dict[str, object]:
        snapshot = _run_model_provider_snapshot(run.input)
        agent = (
            self._session.get(AgentProfile, run.agent_profile_id)
            if run.agent_profile_id is not None
            else None
        )
        live_provider = _agent_model_provider_payload(agent) if agent is not None else {}
        provider = snapshot or live_provider
        return {
            "run_id": run.id,
            "task_id": run.task_id,
            "task_step_id": run.task_step_id,
            "agent_profile_id": run.agent_profile_id,
            "status": run.status,
            "model": run.model or provider.get("selected_model"),
            "provider_snapshot_source": "frozen_run_snapshot" if snapshot else "live_agent",
            "provider": provider.get("provider"),
            "credential_id": _uuid_or_none(provider.get("credential_id")),
            "credential_reference": provider.get("credential_reference"),
            "model_api": provider.get("model_api"),
            "readiness_status": provider.get("readiness_status"),
            "reasons": _string_list(provider.get("reasons")),
            "warnings": _string_list(provider.get("warnings")),
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }


