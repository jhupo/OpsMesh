from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.api.schemas.operations import (
    TeamRuntimeTimelineEventResponse,
    TeamRuntimeTimelineResponse,
    TeamRuntimeTimelineSummaryResponse,
)
from backend.app.audit.models import AuditEvent
from backend.app.operations.models import WorkerLease
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.security.redaction import is_sensitive_payload_key
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue

TEAM_RUNTIME_AUDIT_ACTION_PREFIX = "team.runtime."
TEAM_EXECUTION_ITERATION_ACTION = "team.execution_loop.iteration_ran"
TEAM_RUNTIME_CAPABILITY_KEY = "team_runtime"
TEAM_RUNTIME_MESSAGE_TYPES = {
    "runtime_iteration",
    "team.runtime.bound",
    "team.runtime.continued",
    "team.runtime.ensured",
    "team.runtime.model_provider.fallback_selected",
    "team.runtime.model_provider.fallback_unavailable",
    "team.runtime.model_provider.updated",
    "team.runtime.paused",
    "team.runtime.resumed",
    "team.runtime.started",
    "team.runtime.stopped",
    "team.runtime.worker_failed",
    "team.runtime.worker_retrying",
}
REDACTED_TEXT = "[redacted]"
SECRET_LIKE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*"
        r"['\"]?[^\s,'\";}]+['\"]?"
    ),
    re.compile(r"(?i)\bbase[_ -]?url\s*[:=]\s*['\"]?[^\s,'\";}]+['\"]?"),
)


@dataclass(frozen=True)
class TimelineFilters:
    limit: int = 50
    offset: int = 0
    source_type: str | None = None
    event_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_runs: bool = False
    include_queue: bool = True


@dataclass(frozen=True)
class _TimelineEvent:
    id: str
    source_type: str
    event_type: str
    occurred_at: datetime
    resource_id: str
    message: str
    metadata: dict[str, object]


class TeamRuntimeTimelineService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def timeline(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
        queue: RedisQueue | None = None,
    ) -> TeamRuntimeTimelineResponse | None:
        if not self._team_exists(workspace_id, team_id):
            return None

        runtime_ids = self._team_runtime_ids(workspace_id, team_id)
        events = [
            *self._agent_messages(workspace_id, team_id, filters),
            *self._audit_events(workspace_id, team_id, filters),
            *self._scheduler_scan(workspace_id, team_id, filters),
            *self._blocked_steps(workspace_id, team_id, filters),
            *self._worker_leases(workspace_id, team_id, filters),
            *self._runtime_events(workspace_id, runtime_ids, filters),
        ]
        if filters.include_runs:
            events.extend(self._run_events(workspace_id, team_id, filters))
        if filters.include_queue:
            events.extend(self._queue_events(workspace_id, team_id, filters, queue))

        events = [event for event in events if _matches_filters(event, filters)]
        events.sort(key=lambda event: (event.occurred_at, event.id), reverse=True)
        total = len(events)
        page = events[filters.offset : filters.offset + filters.limit]
        return TeamRuntimeTimelineResponse(
            workspace_id=workspace_id,
            team_id=team_id,
            generated_at=datetime.now(UTC),
            limit=filters.limit,
            offset=filters.offset,
            summary=TeamRuntimeTimelineSummaryResponse(
                total_events=total,
                returned_events=len(page),
                source_counts=_counts(event.source_type for event in events),
                event_type_counts=_counts(event.event_type for event in events),
                include_runs=filters.include_runs,
                include_queue=filters.include_queue,
            ),
            items=[
                TeamRuntimeTimelineEventResponse(
                    id=event.id,
                    source_type=event.source_type,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    resource_id=event.resource_id,
                    message=_redact_secret_like_text(event.message),
                    metadata=_redact_metadata(event.metadata),
                )
                for event in page
            ],
        )

    def _team_exists(self, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == team_id,
                )
            )
            is not None
        )

    def _team_runtime_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.status != "deleted",
            )
        ).all()
        team_id_text = str(team_id)
        return {
            runtime.id
            for runtime in runtimes
            if _team_runtime_team_id(runtime.capabilities) == team_id_text
        }

    def _agent_messages(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        statement = select(AgentMessage).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.agent_team_id == team_id,
            AgentMessage.message_type.in_(TEAM_RUNTIME_MESSAGE_TYPES),
        )
        statement = _apply_time_filters(statement, AgentMessage.created_at, filters)
        messages = self._session.scalars(statement).all()
        return [
            _TimelineEvent(
                id=f"agent_message:{message.id}",
                source_type="agent_message",
                event_type=message.message_type,
                occurred_at=_aware_datetime(message.created_at),
                resource_id=str(message.id),
                message=message.body,
                metadata={
                    "message_id": str(message.id),
                    "thread_id": str(message.thread_id),
                    "task_id": str(message.task_id) if message.task_id is not None else None,
                    "agent_team_id": str(message.agent_team_id)
                    if message.agent_team_id is not None
                    else None,
                    "sender_agent_profile_id": str(message.sender_agent_profile_id)
                    if message.sender_agent_profile_id is not None
                    else None,
                    "recipient_agent_profile_id": str(message.recipient_agent_profile_id)
                    if message.recipient_agent_profile_id is not None
                    else None,
                    "status": message.status,
                    "payload": dict(message.payload or {}),
                },
            )
            for message in messages
        ]

    def _audit_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        member_ids = self._team_member_ids(workspace_id, team_id)
        target_pairs = [("agent_team", str(team_id))] + [
            ("agent_team_member", str(member_id)) for member_id in member_ids
        ]
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.target_type.in_({"agent_team", "agent_team_member"}),
            AuditEvent.target_id.in_({target_id for _target_type, target_id in target_pairs}),
        )
        statement = _apply_time_filters(statement, AuditEvent.created_at, filters)
        audit_events = self._session.scalars(statement).all()
        return [
            _TimelineEvent(
                id=f"audit_event:{event.id}",
                source_type="audit_event",
                event_type=event.action,
                occurred_at=_aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=event.action,
                metadata={
                    "audit_event_id": str(event.id),
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "user_id": str(event.user_id) if event.user_id is not None else None,
                    "agent_run_id": str(event.agent_run_id)
                    if event.agent_run_id is not None
                    else None,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "audit_metadata": dict(event.audit_metadata or {}),
                },
            )
            for event in audit_events
            if (event.target_type, event.target_id) in target_pairs
            if event.action.startswith(TEAM_RUNTIME_AUDIT_ACTION_PREFIX)
            or event.action == "team.member_model_provider.updated"
            or event.action == TEAM_EXECUTION_ITERATION_ACTION
        ]

    def _team_member_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        return set(
            self._session.scalars(
                select(AgentTeamMember.id).where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
            ).all()
        )

    def _scheduler_scan(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        team = self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )
        if team is None:
            return []
        policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
        runtime_metadata = policy.get(TEAM_RUNTIME_CAPABILITY_KEY)
        if not isinstance(runtime_metadata, dict):
            return []
        scan = runtime_metadata.get("last_scheduler_scan")
        if not isinstance(scan, dict):
            return []
        scanned_at = _datetime_from_value(scan.get("scanned_at"))
        if scanned_at is None or not _within(scanned_at, filters):
            return []
        status = scan.get("status")
        event_status = status if isinstance(status, str) and status else "recorded"
        reason = scan.get("reason")
        reason_text = f": {reason}" if isinstance(reason, str) and reason else ""
        return [
            _TimelineEvent(
                id=f"scheduler_scan:{team_id}:{scan.get('window') or scanned_at.isoformat()}",
                source_type="scheduler_scan",
                event_type=f"team.runtime.scheduler.{event_status}",
                occurred_at=_aware_datetime(scanned_at),
                resource_id=str(team_id),
                message=f"Team runtime scheduler scan {event_status}{reason_text}",
                metadata={
                    "team_id": str(team_id),
                    "workspace_id": str(workspace_id),
                    "scan": dict(scan),
                },
            )
        ]

    def _worker_leases(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        statement = select(WorkerLease).where(
            WorkerLease.workspace_id == workspace_id,
            WorkerLease.job_type == JobType.TEAM_EXECUTION_LOOP.value,
            WorkerLease.resource_id == team_id,
        )
        leases = self._session.scalars(statement).all()
        events: list[_TimelineEvent] = []
        for lease in leases:
            metadata = {
                "worker_lease_id": str(lease.id),
                "worker_id": lease.worker_id,
                "queue_name": lease.queue_name,
                "job_id": str(lease.job_id),
                "job_type": lease.job_type,
                "resource_id": str(lease.resource_id),
                "attempt": lease.attempt,
                "lease_metadata": dict(lease.lease_metadata or {}),
            }
            if _within(lease.started_at, filters):
                events.append(
                    _TimelineEvent(
                        id=f"worker_lease:{lease.id}:started",
                        source_type="worker_lease",
                        event_type="worker_lease.started",
                        occurred_at=_aware_datetime(lease.started_at),
                        resource_id=str(lease.id),
                        message=f"Worker lease started on {lease.worker_id}",
                        metadata=metadata | {"status": lease.status},
                    )
                )
            if lease.finished_at is not None and _within(lease.finished_at, filters):
                events.append(
                    _TimelineEvent(
                        id=f"worker_lease:{lease.id}:finished",
                        source_type="worker_lease",
                        event_type=f"worker_lease.{lease.status}",
                        occurred_at=_aware_datetime(lease.finished_at),
                        resource_id=str(lease.id),
                        message=f"Worker lease {lease.status} on {lease.worker_id}",
                        metadata=metadata | {"status": lease.status},
                    )
                )
        return events

    def _blocked_steps(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        statement = (
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
            )
        )
        statement = _apply_time_filters(statement, TaskStep.updated_at, filters)
        rows = self._session.execute(statement).all()
        events: list[_TimelineEvent] = []
        for step, task in rows:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            reason = dependencies.get("blocked_reason")
            reason_text = reason if isinstance(reason, str) and reason else "unknown"
            events.append(
                _TimelineEvent(
                    id=f"task_step:{step.id}:scheduling_blocked",
                    source_type="task_step",
                    event_type="team.runtime.step.scheduling_blocked",
                    occurred_at=_aware_datetime(step.updated_at),
                    resource_id=str(step.id),
                    message=f"Team runtime step scheduling blocked: {reason_text}",
                    metadata={
                        "workspace_id": str(workspace_id),
                        "team_id": str(team_id),
                        "task_id": str(task.id),
                        "task_title": task.title,
                        "task_step_id": str(step.id),
                        "step_title": step.title,
                        "step_status": step.status,
                        "assigned_agent_profile_id": str(step.assigned_agent_profile_id)
                        if step.assigned_agent_profile_id is not None
                        else None,
                        "runtime_space_id": str(step.runtime_space_id)
                        if step.runtime_space_id is not None
                        else None,
                        "blocked_reason": reason_text,
                        "blocked_details": dict(dependencies.get("blocked_details") or {}),
                    },
                )
            )
        return events

    def _runtime_events(
        self,
        workspace_id: UUID,
        runtime_ids: set[UUID],
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        if not runtime_ids:
            return []
        statement = select(RuntimeEvent).where(
            RuntimeEvent.workspace_id == workspace_id,
            RuntimeEvent.workspace_runtime_id.in_(runtime_ids),
        )
        statement = _apply_time_filters(statement, RuntimeEvent.created_at, filters)
        runtime_events = self._session.scalars(statement).all()
        return [
            _TimelineEvent(
                id=f"runtime_event:{event.id}",
                source_type="runtime_event",
                event_type=event.event_type,
                occurred_at=_aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=event.message,
                metadata={
                    "runtime_event_id": str(event.id),
                    "workspace_runtime_id": str(event.workspace_runtime_id),
                    "runtime_space_id": str(event.runtime_space_id)
                    if event.runtime_space_id is not None
                    else None,
                    "event_metadata": dict(event.event_metadata or {}),
                },
            )
            for event in runtime_events
        ]

    def _run_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[_TimelineEvent]:
        run_ids = self._team_run_event_ids(workspace_id, team_id)
        if not run_ids:
            return []
        events_statement = select(RunEvent).where(
            RunEvent.workspace_id == workspace_id,
            RunEvent.agent_run_id.in_(run_ids),
        )
        events_statement = _apply_time_filters(events_statement, RunEvent.created_at, filters)
        run_events = self._session.scalars(events_statement).all()
        return [
            _TimelineEvent(
                id=f"run_event:{event.id}",
                source_type="run_event",
                event_type=event.event_type,
                occurred_at=_aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=event.message,
                metadata={
                    "run_event_id": str(event.id),
                    "agent_run_id": str(event.agent_run_id),
                    "sequence": event.sequence,
                    "event_metadata": dict(event.event_metadata or {}),
                },
            )
            for event in run_events
        ]

    def _team_run_event_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        return set(
            self._session.scalars(
                select(AgentRun.id)
                .join(Task, Task.id == AgentRun.task_id)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    Task.workspace_id == workspace_id,
                    Task.agent_team_id == team_id,
                )
            ).all()
        )

    def _queue_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
        queue: RedisQueue | None,
    ) -> list[_TimelineEvent]:
        if queue is None:
            return []
        try:
            jobs_by_state = {
                "queued": queue.list_queued(
                    filters.limit,
                    workspace_id=workspace_id,
                    job_type=JobType.TEAM_EXECUTION_LOOP,
                    resource_id=team_id,
                ),
                "scheduled_retry": queue.list_scheduled_retries(
                    filters.limit,
                    workspace_id=workspace_id,
                    job_type=JobType.TEAM_EXECUTION_LOOP,
                    resource_id=team_id,
                ),
                "dead_letter": queue.list_dead_letters(
                    filters.limit,
                    workspace_id=workspace_id,
                    job_type=JobType.TEAM_EXECUTION_LOOP,
                    resource_id=team_id,
                ),
            }
        except (OSError, RedisError, TimeoutError) as exc:
            return [
                _TimelineEvent(
                    id=f"queue_job:unavailable:{team_id}",
                    source_type="queue_job",
                    event_type="team.runtime.queue.unavailable",
                    occurred_at=datetime.now(UTC),
                    resource_id=str(team_id),
                    message="Team execution loop queue snapshot is unavailable",
                    metadata={
                        "queue_name": queue.queue_name,
                        "job_type": JobType.TEAM_EXECUTION_LOOP.value,
                        "workspace_id": str(workspace_id),
                        "resource_id": str(team_id),
                        "error_type": type(exc).__name__,
                        "error": _redact_secret_like_text(str(exc)),
                    },
                )
            ]
        events: list[_TimelineEvent] = []
        for state, jobs in jobs_by_state.items():
            for job in jobs:
                occurred_at = _queue_job_time(job)
                if not _within(occurred_at, filters):
                    continue
                events.append(
                    _TimelineEvent(
                        id=f"queue_job:{state}:{job.job_id}",
                        source_type="queue_job",
                        event_type=f"team.runtime.queue.{state}",
                        occurred_at=occurred_at,
                        resource_id=str(job.job_id),
                        message=f"Team execution loop job is {state}",
                        metadata={
                            "state": state,
                            "queue_name": queue.queue_name,
                            "job_id": str(job.job_id),
                            "job_type": job.job_type.value,
                            "workspace_id": str(job.workspace_id),
                            "resource_id": str(job.resource_id),
                            "attempt": job.attempt,
                            "max_attempts": job.max_attempts,
                            "priority": job.priority,
                            "last_error": _redact_secret_like_text(job.last_error)
                            if job.last_error is not None
                            else None,
                            "last_error_type": job.last_error_type,
                            "last_failed_at": job.last_failed_at.isoformat()
                            if job.last_failed_at is not None
                            else None,
                            "routing": _redact_metadata(dict(job.routing)),
                            "trace": _redact_metadata(job.trace_metadata()),
                        },
                    )
                )
        return events


def _apply_time_filters(statement: Any, column: Any, filters: TimelineFilters) -> Any:
    if filters.since is not None:
        statement = statement.where(column >= filters.since)
    if filters.until is not None:
        statement = statement.where(column <= filters.until)
    return statement


def _matches_filters(event: _TimelineEvent, filters: TimelineFilters) -> bool:
    if filters.source_type is not None and event.source_type != filters.source_type:
        return False
    if filters.event_type is not None and event.event_type != filters.event_type:
        return False
    return _within(event.occurred_at, filters)


def _within(value: datetime, filters: TimelineFilters) -> bool:
    occurred_at = _aware_datetime(value)
    if filters.since is not None and occurred_at < _aware_datetime(filters.since):
        return False
    return not (filters.until is not None and occurred_at > _aware_datetime(filters.until))


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _team_runtime_team_id(capabilities: dict[str, object]) -> str | None:
    team_runtime = capabilities.get(TEAM_RUNTIME_CAPABILITY_KEY)
    if not isinstance(team_runtime, dict):
        return None
    team_id = team_runtime.get("team_id")
    return team_id if isinstance(team_id, str) else None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _datetime_from_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_datetime(value)
    if isinstance(value, str):
        try:
            return _aware_datetime(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _queue_job_time(job: Any) -> datetime:
    return _aware_datetime(job.last_failed_at or job.created_at)


def _redact_secret_like_text(value: str) -> str:
    redacted = value
    for pattern in SECRET_LIKE_PATTERNS:
        redacted = pattern.sub(REDACTED_TEXT, redacted)
    return redacted


def _redact_metadata(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            redacted[key_text] = (
                REDACTED_TEXT if is_sensitive_payload_key(key_text) else _redact_metadata(item)
            )
        return redacted
    if isinstance(value, list):
        return [_redact_metadata(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_like_text(value)
    return value
