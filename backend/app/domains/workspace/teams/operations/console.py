from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import dict_list, dict_or_empty
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.agents.sessions.management import PersistentAgentSessionManagementService
from backend.app.domains.agents.sessions.views import PersistentSessionSummary
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUSES
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.domains.workspace.teams.operations.command_center import TeamCommandCenterService
from backend.app.domains.workspace.teams.operations.console_mailbox import (
    TeamOperationsMailboxReader,
)
from backend.app.domains.workspace.teams.operations.console_runtime_payloads import (
    runtime_blocked_step_suggested_actions,
    runtime_blocked_steps_payload,
    runtime_payload,
    runtime_queue_suggested_actions,
    team_loop_queue_payload,
)
from backend.app.domains.workspace.teams.operations.views import (
    _dict,
    _int_value,
    _positive_int,
    _redacted_dict_or_none,
    _visible_task_policy,
)
from backend.app.domains.workspace.teams.providers.management import TeamProviderManagementBuilder
from backend.app.domains.workspace.teams.providers.views import _agent_model_provider_payload
from backend.app.domains.workspace.teams.runtime.scheduling_blocks import (
    scheduled_run_blocking_summary,
)
from backend.app.domains.workspace.teams.runtime.service import TeamRuntimeService, TeamRuntimeState
from backend.app.runtime.workers.queue import RedisQueue

PROVIDER_RUN_STATUSES = ACTIVE_RUN_STATUSES


class TeamOperationsConsoleService:
    """Operator-facing aggregate for a persistent team/company runtime."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_console(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        queue: RedisQueue | None = None,
        include_completed: bool = False,
        queue_limit: int = 50,
        message_limit: int = 10,
        session_limit: int = 100,
    ) -> dict[str, object] | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None
        runtime_state = TeamRuntimeService(self._session).get_state(
            workspace_id=workspace_id, team_id=team_id
        )
        if runtime_state is None:
            return None
        command_center = TeamCommandCenterService(self._session).get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        sessions = PersistentAgentSessionManagementService(self._session).list_sessions(
            workspace_id=workspace_id, agent_team_id=team_id, limit=session_limit, offset=0
        )
        members = self._members(team)
        session_by_agent_id = {
            summary.agent_profile_id: summary
            for summary in sessions
            if summary.agent_profile_id is not None
        }
        team_session = (
            next(
                (summary for summary in sessions if summary.id == runtime_state.team_session_id),
                None,
            )
            if runtime_state.team_session_id is not None
            else None
        )
        runtime_queue = team_loop_queue_payload(
            queue=queue, workspace_id=workspace_id, team_id=team_id, limit=max(queue_limit, 0)
        )
        provider_management = TeamProviderManagementBuilder(self._session).payload(
            workspace_id=workspace_id, members=members
        )
        runtime_blocking = scheduled_run_blocking_summary(
            self._session, workspace_id=workspace_id, team_id=team_id
        )
        mailbox = TeamOperationsMailboxReader(self._session).team_payload(
            runtime_state=runtime_state, message_limit=max(message_limit, 0)
        )
        controls = _controls_payload(
            runtime_state, command_center, runtime_queue, provider_management, runtime_blocking
        )
        mailbox_reader = TeamOperationsMailboxReader(self._session)
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "team": _team_payload(team),
            "runtime": runtime_payload(
                runtime_state, runtime_queue=runtime_queue, runtime_blocking=runtime_blocking
            ),
            "command_center": _command_center_payload(command_center),
            "members": [
                self._member_payload(
                    member=member,
                    session=session_by_agent_id.get(member.agent_profile_id),
                    mailbox_reader=mailbox_reader,
                )
                for member in members
            ],
            "provider_management": provider_management,
            "sessions": {
                "team_session": _session_payload(team_session),
                "member_sessions": [
                    _session_payload(summary)
                    for summary in sessions
                    if summary.scope_type == "team_agent"
                ],
                "total": len(sessions),
            },
            "mailbox": mailbox,
            "controls": controls,
            "readiness": _readiness_payload(
                runtime_state,
                command_center,
                runtime_queue,
                provider_management,
                runtime_blocking,
                mailbox,
                controls,
            ),
        }

    def _team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
        )

    def _members(self, team: AgentTeam) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == team.workspace_id,
                    AgentTeamMember.agent_team_id == team.id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def _member_payload(
        self,
        *,
        member: AgentTeamMember,
        session: PersistentSessionSummary | None,
        mailbox_reader: TeamOperationsMailboxReader,
    ) -> dict[str, object]:
        agent = self._session.get(AgentProfile, member.agent_profile_id)
        return {
            "team_member_id": member.id,
            "agent_profile_id": member.agent_profile_id,
            "agent": _agent_payload(agent),
            "reports_to_member_id": member.reports_to_member_id,
            "team_role": member.team_role,
            "department": member.department,
            "position_title": member.position_title,
            "responsibilities": list(member.responsibilities),
            "accepts_tasks": member.accepts_tasks,
            "max_concurrent_tasks": member.max_concurrent_tasks,
            "status": member.status,
            "session": _session_payload(session),
            "mailbox": mailbox_reader.agent_payload(
                workspace_id=member.workspace_id,
                agent_team_id=member.agent_team_id,
                agent_profile_id=member.agent_profile_id,
            ),
        }


def _agent_payload(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
        "model": agent.model,
        "model_provider_credential_id": agent.model_provider_credential_id,
        "model_provider": _agent_model_provider_payload(agent),
    }


def _controls_payload(
    runtime_state: TeamRuntimeState,
    command_center: dict[str, object] | None,
    runtime_queue: dict[str, object],
    provider_management: dict[str, object],
    runtime_blocking: dict[str, object],
) -> dict[str, object]:
    action_plan = dict_list(command_center.get("action_plan")) if command_center is not None else []
    provider_management_actions = dict_list(provider_management.get("suggested_actions"))
    return {
        "can_start": runtime_state.status != "running",
        "can_pause": runtime_state.status == "running",
        "can_resume": runtime_state.status == "paused",
        "can_stop": runtime_state.status != "stopped",
        "can_ensure_workspace_runtime": runtime_state.workspace_runtime_id is None
        or runtime_state.runtime_status != "running"
        or runtime_state.runtime_health in {"stale", "degraded"},
        "suggested_actions": [
            item
            for item in [*provider_management_actions, *action_plan]
            if isinstance(item, dict)
            and item.get("source") in {"provider_management", "provider_readiness", "team_runtime"}
        ]
        + runtime_queue_suggested_actions(runtime_queue)
        + runtime_blocked_step_suggested_actions(runtime_blocking),
    }


def _readiness_payload(
    runtime_state: TeamRuntimeState,
    command_center: dict[str, object] | None,
    runtime_queue: dict[str, object],
    provider_management: dict[str, object],
    runtime_blocking: dict[str, object],
    mailbox: dict[str, object],
    controls: dict[str, object],
) -> dict[str, object]:
    runtime_metadata = dict(runtime_state.metadata)
    stall_count = _int_value(runtime_metadata.get("stall_count"))
    stall_threshold = _positive_int(runtime_metadata.get("stall_threshold"), 3)
    stalled_at = runtime_metadata.get("stalled_at")
    provider_blocked = any(
        item.get("readiness_status") == "blocked"
        for item in dict_list(provider_management.get("agent_bindings"))
    )
    blocked_step_count = _int_value(runtime_blocked_steps_payload(runtime_blocking).get("count"))
    queue_backlog = (
        _int_value(runtime_queue.get("queued"))
        + _int_value(runtime_queue.get("scheduled_retry"))
        + _int_value(runtime_queue.get("dead_letter"))
    )
    suggested_actions = dict_list(controls.get("suggested_actions"))
    suggested_actions.sort(key=lambda item: _int_value(item.get("priority")), reverse=True)
    next_action = suggested_actions[0] if suggested_actions else None
    readiness_status = _readiness_status(
        runtime_state=runtime_state,
        stalled=bool(stalled_at) or stall_count >= stall_threshold,
        provider_blocked=provider_blocked,
        blocked_step_count=blocked_step_count,
        queue_backlog=queue_backlog,
    )
    return redact_sensitive_payload(
        {
            "status": readiness_status,
            "ready": readiness_status == "ready",
            "runtime_health": runtime_state.runtime_health,
            "runtime_status": runtime_state.status,
            "workspace_runtime_status": runtime_state.runtime_status,
            "stall": {
                "count": stall_count,
                "threshold": stall_threshold,
                "reason": runtime_metadata.get("stall_reason"),
                "stalled_at": stalled_at,
            },
            "mailbox_unread_count": _int_value(mailbox.get("unread_count")),
            "queue_backlog": queue_backlog,
            "blocked_step_count": blocked_step_count,
            "provider_blocked": provider_blocked,
            "action_plan_count": _int_value(
                _dict(command_center.get("summary") if command_center else {}).get(
                    "action_plan_count"
                )
            ),
            "next_operator_action": next_action,
        }
    )


def _readiness_status(
    *,
    runtime_state: TeamRuntimeState,
    stalled: bool,
    provider_blocked: bool,
    blocked_step_count: int,
    queue_backlog: int,
) -> str:
    if runtime_state.status in {"paused", "stopped"}:
        return runtime_state.status
    if stalled:
        return "stalled"
    if provider_blocked:
        return "provider_blocked"
    if runtime_state.runtime_health in {"stale", "degraded"}:
        return runtime_state.runtime_health
    if blocked_step_count > 0:
        return "blocked"
    if queue_backlog > 0:
        return "working"
    if runtime_state.runtime_health == "healthy" and runtime_state.runtime_status == "running":
        return "ready"
    return runtime_state.runtime_health or "unknown"


def _team_payload(team: AgentTeam) -> dict[str, object]:
    return {
        "id": team.id,
        "workspace_id": team.workspace_id,
        "name": team.name,
        "team_type": team.team_type,
        "description": team.description,
        "status": team.status,
        "manager_agent_profile_id": team.manager_agent_profile_id,
        "runtime_space_id": team.runtime_space_id,
        "coordination_rules": dict(team.coordination_rules),
        "default_task_policy": _visible_task_policy(team.default_task_policy),
    }


def _command_center_payload(command_center: dict[str, object] | None) -> dict[str, object]:
    if command_center is None:
        return {
            "summary": {},
            "runtime": {},
            "provider_readiness": {},
            "operating_policy": {},
            "memory_summary": {},
            "overview": None,
            "queues": None,
            "action_plan": [],
        }
    return {
        "workspace_id": command_center.get("workspace_id"),
        "team_id": command_center.get("team_id"),
        "generated_at": command_center.get("generated_at"),
        "summary": redact_sensitive_payload(dict_or_empty(command_center.get("summary"))),
        "runtime": redact_sensitive_payload(dict_or_empty(command_center.get("runtime"))),
        "provider_readiness": redact_sensitive_payload(
            dict_or_empty(command_center.get("provider_readiness"))
        ),
        "operating_policy": redact_sensitive_payload(
            dict_or_empty(command_center.get("operating_policy"))
        ),
        "memory_summary": redact_sensitive_payload(
            dict_or_empty(command_center.get("memory_summary"))
        ),
        "overview": redact_sensitive_payload(dict_or_empty(command_center.get("overview"))),
        "queues": redact_sensitive_payload(dict_or_empty(command_center.get("queues"))),
        "action_plan": [
            redact_sensitive_payload(item) for item in dict_list(command_center.get("action_plan"))
        ],
    }


def _session_payload(summary: PersistentSessionSummary | None) -> dict[str, object] | None:
    if summary is None:
        return None
    return {
        "id": summary.id,
        "session_key": summary.session_key,
        "scope_type": summary.scope_type,
        "scope_id": summary.scope_id,
        "status": summary.status,
        "agent_profile_id": summary.agent_profile_id,
        "agent_team_id": summary.agent_team_id,
        "item_count": summary.item_count,
        "openai_conversation_id": summary.openai_conversation_id,
        "latest_item_metadata": _redacted_dict_or_none(summary.latest_item_metadata),
        "updated_at": summary.updated_at,
    }
