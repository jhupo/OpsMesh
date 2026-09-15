from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.common.values import dict_list, dict_or_empty
from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.domains.agents.messages.models import AgentMessage
from backend.app.domains.agents.models import AgentProfile
from backend.app.domains.agents.sessions.management import PersistentAgentSessionManagementService
from backend.app.domains.agents.sessions.views import PersistentSessionSummary
from backend.app.domains.orchestration.workflows.statuses import ACTIVE_RUN_STATUSES
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.domains.workspace.teams.operations.command_center import TeamCommandCenterService
from backend.app.domains.workspace.teams.operations.views import (
    _datetime_or_none,
    _dict,
    _int_value,
    _positive_int,
    _preview,
    _redacted_dict_or_none,
    _unique_strings,
    _visible_task_policy,
)
from backend.app.domains.workspace.teams.providers.management import TeamProviderManagementBuilder
from backend.app.domains.workspace.teams.providers.views import _agent_model_provider_payload
from backend.app.domains.workspace.teams.runtime.scheduling_blocks import (
    scheduled_run_blocking_summary,
)
from backend.app.domains.workspace.teams.runtime.service import TeamRuntimeService, TeamRuntimeState
from backend.app.runtime.workers.contracts import JobPayload, JobType
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
        runtime_queue = _team_loop_queue_payload(
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
            "runtime": _runtime_payload(
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
        + _runtime_queue_suggested_actions(runtime_queue)
        + _runtime_blocked_step_suggested_actions(runtime_blocking),
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
    blocked_step_count = _int_value(_runtime_blocked_steps_payload(runtime_blocking).get("count"))
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


class TeamOperationsMailboxReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def agent_payload(
        self, *, workspace_id: UUID, agent_team_id: UUID, agent_profile_id: UUID
    ) -> dict[str, object]:
        unread_count = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id == agent_team_id,
                AgentMessage.recipient_agent_profile_id == agent_profile_id,
                AgentMessage.read_at.is_(None),
                AgentMessage.status != "read",
            )
        )
        latest_message_at = self._session.scalar(
            select(func.max(AgentMessage.created_at)).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id == agent_team_id,
                (AgentMessage.sender_agent_profile_id == agent_profile_id)
                | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
            )
        )
        return {"unread_count": int(unread_count or 0), "latest_message_at": latest_message_at}

    def team_payload(
        self, *, runtime_state: TeamRuntimeState, message_limit: int
    ) -> dict[str, object]:
        if runtime_state.thread_id is None:
            return {"thread_id": None, "unread_count": 0, "latest_messages": []}
        messages = list(
            self._session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.workspace_id == runtime_state.workspace_id,
                    AgentMessage.thread_id == runtime_state.thread_id,
                    AgentMessage.agent_team_id == runtime_state.team_id,
                )
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
                .limit(message_limit)
            )
        )
        unread_count = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == runtime_state.workspace_id,
                AgentMessage.thread_id == runtime_state.thread_id,
                AgentMessage.agent_team_id == runtime_state.team_id,
                AgentMessage.read_at.is_(None),
                AgentMessage.status != "read",
            )
        )
        return {
            "thread_id": runtime_state.thread_id,
            "unread_count": int(unread_count or 0),
            "latest_messages": [_message_payload(message) for message in messages],
        }


def _message_payload(message: AgentMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "task_id": message.task_id,
        "agent_team_id": message.agent_team_id,
        "sender_agent_profile_id": message.sender_agent_profile_id,
        "recipient_agent_profile_id": message.recipient_agent_profile_id,
        "message_type": message.message_type,
        "status": message.status,
        "read_at": message.read_at,
        "created_at": message.created_at,
        "body_preview": _preview(message.body),
        "payload": redact_sensitive_payload(dict(message.payload)),
    }


def _runtime_payload(
    state: TeamRuntimeState,
    *,
    runtime_queue: dict[str, object],
    runtime_blocking: dict[str, object],
) -> dict[str, object]:
    return {
        "status": state.status,
        "workspace_runtime_id": state.workspace_runtime_id,
        "runtime_status": state.runtime_status,
        "runtime_space_id": state.runtime_space_id,
        "runtime_health": state.runtime_health,
        "thread_id": state.thread_id,
        "team_session_id": state.team_session_id,
        "team_session_key": state.team_session_key,
        "member_session_count": state.member_session_count,
        "member_agent_ids": state.member_agent_ids,
        "last_iteration": _redacted_dict_or_none(state.last_iteration),
        "last_message_at": state.last_message_at,
        "operating_policy": redact_sensitive_payload(dict(state.operating_policy)),
        "memory_summary": redact_sensitive_payload(dict(state.memory_summary)),
        "scheduling": redact_sensitive_payload(
            _runtime_scheduling_payload(state.metadata, state.generated_at)
        ),
        "blocked_steps": redact_sensitive_payload(_runtime_blocked_steps_payload(runtime_blocking)),
        "queue": redact_sensitive_payload(runtime_queue),
        "ready": state.status == "running"
        and state.workspace_runtime_id is not None
        and (state.runtime_status == "running")
        and (state.runtime_health == "healthy"),
        "metadata": redact_sensitive_payload(dict(state.metadata)),
    }


def _team_loop_queue_payload(
    *, queue: RedisQueue | None, workspace_id: UUID, team_id: UUID, limit: int
) -> dict[str, object]:
    if queue is None:
        return {
            "available": False,
            "queue_name": None,
            "queued": 0,
            "scheduled_retry": 0,
            "dead_letter": 0,
            "latest_jobs": [],
        }
    scan_limit = min(max(limit, 1), 200)
    queued = queue.list_queued(
        scan_limit,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team_id,
    )
    scheduled_retry = queue.list_scheduled_retries(
        scan_limit,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team_id,
    )
    dead_letter = queue.list_dead_letters(
        scan_limit,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=team_id,
    )
    latest_jobs = [
        *_job_payloads("queued", queued),
        *_job_payloads("scheduled_retry", scheduled_retry),
        *_job_payloads("dead_letter", dead_letter),
    ]
    latest_jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "available": True,
        "queue_name": queue.queue_name,
        "job_type": JobType.TEAM_EXECUTION_LOOP.value,
        "queued": len(queued),
        "scheduled_retry": len(scheduled_retry),
        "dead_letter": len(dead_letter),
        "latest_jobs": latest_jobs[:10],
    }


def _job_payloads(state: str, jobs: list[JobPayload]) -> list[dict[str, object]]:
    return [
        {
            "state": state,
            "job_id": str(job.job_id),
            "resource_id": str(job.resource_id),
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "priority": job.priority,
            "last_error": "[redacted]" if job.last_error is not None else None,
            "last_error_type": job.last_error_type,
            "last_failed_at": job.last_failed_at.isoformat()
            if job.last_failed_at is not None
            else None,
            "created_at": job.created_at.isoformat(),
            "routing": redact_sensitive_payload(dict(job.routing)),
        }
        for job in jobs
    ]


def _runtime_queue_suggested_actions(runtime_queue: dict[str, object]) -> list[dict[str, object]]:
    if runtime_queue.get("available") is not True:
        return []
    actions: list[dict[str, object]] = []
    dead_letter = _int_value(runtime_queue.get("dead_letter"))
    scheduled_retry = _int_value(runtime_queue.get("scheduled_retry"))
    queued = _int_value(runtime_queue.get("queued"))
    if dead_letter > 0:
        actions.append(
            {
                "source": "team_runtime_queue",
                "automation": "team_runtime_control",
                "action": "inspect_dead_letter",
                "priority": 95,
                "reason": "team_execution_loop_dead_letter",
                "count": dead_letter,
            }
        )
    if scheduled_retry > 0:
        actions.append(
            {
                "source": "team_runtime_queue",
                "automation": "team_runtime_control",
                "action": "monitor_retry",
                "priority": 60,
                "reason": "team_execution_loop_retry_scheduled",
                "count": scheduled_retry,
            }
        )
    if queued > 1:
        actions.append(
            {
                "source": "team_runtime_queue",
                "automation": "team_runtime_control",
                "action": "inspect_queue",
                "priority": 40,
                "reason": "team_execution_loop_queue_backlog",
                "count": queued,
            }
        )
    return actions


def _runtime_blocked_steps_payload(runtime_blocking: dict[str, object]) -> dict[str, object]:
    steps = dict_list(runtime_blocking.get("blocked_steps"))
    return {
        "count": len(steps),
        "blocked_reasons": _dict(runtime_blocking.get("blocked_reasons")),
        "latest": steps[:10],
        "truncated": len(steps) > 10,
    }


def _runtime_blocked_step_suggested_actions(
    runtime_blocking: dict[str, object],
) -> list[dict[str, object]]:
    payload = _runtime_blocked_steps_payload(runtime_blocking)
    count = _int_value(payload.get("count"))
    if count <= 0:
        return []
    steps = dict_list(payload.get("latest"))
    return [
        {
            "source": "team_runtime",
            "automation": "team_runtime_control",
            "action": "review_scheduling_blocks",
            "priority": 90,
            "reason": "team_runtime_step_scheduling_blocked",
            "count": count,
            "blocked_reasons": payload["blocked_reasons"],
            "task_ids": _unique_strings(item.get("task_id") for item in steps),
            "task_step_ids": _unique_strings(item.get("task_step_id") for item in steps),
        }
    ]


def _runtime_scheduling_payload(
    metadata: dict[str, object], generated_at: datetime
) -> dict[str, object]:
    scheduling_policy = _dict(metadata.get("scheduling_policy"))
    last_iteration = _dict(metadata.get("last_iteration"))
    last_recorded_at = _datetime_or_none(last_iteration.get("recorded_at"))
    last_heartbeat_at = _datetime_or_none(metadata.get("last_heartbeat_at"))
    anchor = last_recorded_at or last_heartbeat_at
    loop_interval_seconds = _positive_int(
        scheduling_policy.get("loop_interval_seconds"),
        _positive_int(metadata.get("loop_interval_seconds"), 300),
    )
    next_due_at = anchor + timedelta(seconds=loop_interval_seconds) if anchor is not None else None
    return {
        "scheduled_loop_enabled": scheduling_policy.get("scheduled_loop_enabled", True)
        is not False,
        "loop_interval_seconds": loop_interval_seconds,
        "priority": _positive_int(scheduling_policy.get("priority"), 5),
        "last_iteration_at": anchor,
        "next_due_at": next_due_at,
        "due": next_due_at is None or generated_at >= next_due_at,
        "last_scheduler_scan": _dict(metadata.get("last_scheduler_scan")),
    }
