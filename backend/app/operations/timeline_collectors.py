from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.agents.models import AgentProfile
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.operations.models import WorkerLease
from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_utils import (
    TEAM_RUNTIME_CAPABILITY_KEY,
    apply_time_filters,
    aware_datetime,
    configured_mcp_tools,
    datetime_from_value,
    mcp_governance_message,
    queue_job_time,
    redact_metadata,
    redact_secret_like_text,
    team_runtime_team_id,
    within,
)
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue

TEAM_RUNTIME_AUDIT_ACTION_PREFIX = "team.runtime."
TEAM_EXECUTION_ITERATION_ACTION = "team.execution_loop.iteration_ran"
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


class TeamRuntimeTimelineCollector:
    def __init__(self, session: Session):
        self._session = session

    def team_exists(self, workspace_id: UUID, team_id: UUID) -> bool:
        return self._team_exists(workspace_id, team_id)

    def collect(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
        queue: RedisQueue | None,
    ) -> list[TimelineEvent]:
        runtime_ids = self._team_runtime_ids(workspace_id, team_id)
        events = [
            *self._agent_messages(workspace_id, team_id, filters),
            *self._audit_events(workspace_id, team_id, filters),
            *self._scheduler_scan(workspace_id, team_id, filters),
            *self._blocked_steps(workspace_id, team_id, filters),
            *self._mcp_governance_events(workspace_id, team_id, filters),
            *self._worker_leases(workspace_id, team_id, filters),
            *self._runtime_events(workspace_id, runtime_ids, filters),
        ]
        if filters.include_runs:
            events.extend(self._run_events(workspace_id, team_id, filters))
        if filters.include_queue:
            events.extend(self._queue_events(workspace_id, team_id, filters, queue))
        return events

    def _team_exists(self, workspace_id: UUID, team_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id
                )
            )
            is not None
        )

    def _team_runtime_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id, WorkspaceRuntime.status != "deleted"
            )
        ).all()
        team_id_text = str(team_id)
        return {
            runtime.id
            for runtime in runtimes
            if team_runtime_team_id(runtime.capabilities) == team_id_text
        }

    def _agent_messages(
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        statement = select(AgentMessage).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.agent_team_id == team_id,
            AgentMessage.message_type.in_(TEAM_RUNTIME_MESSAGE_TYPES),
        )
        statement = apply_time_filters(statement, AgentMessage.created_at, filters)
        messages = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"agent_message:{message.id}",
                source_type="agent_message",
                event_type=message.message_type,
                occurred_at=aware_datetime(message.created_at),
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
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        member_ids = self._team_member_ids(workspace_id, team_id)
        target_pairs = [("agent_team", str(team_id))] + [
            ("agent_team_member", str(member_id)) for member_id in member_ids
        ]
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.target_type.in_({"agent_team", "agent_team_member"}),
            AuditEvent.target_id.in_({target_id for _target_type, target_id in target_pairs}),
        )
        statement = apply_time_filters(statement, AuditEvent.created_at, filters)
        audit_events = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"audit_event:{event.id}",
                source_type="audit_event",
                event_type=event.action,
                occurred_at=aware_datetime(event.created_at),
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
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        team = self._session.scalar(
            select(AgentTeam).where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
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
        scanned_at = datetime_from_value(scan.get("scanned_at"))
        if scanned_at is None or not within(scanned_at, filters):
            return []
        status = scan.get("status")
        event_status = status if isinstance(status, str) and status else "recorded"
        reason = scan.get("reason")
        reason_text = f": {reason}" if isinstance(reason, str) and reason else ""
        return [
            TimelineEvent(
                id=f"scheduler_scan:{team_id}:{scan.get('window') or scanned_at.isoformat()}",
                source_type="scheduler_scan",
                event_type=f"team.runtime.scheduler.{event_status}",
                occurred_at=aware_datetime(scanned_at),
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
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        statement = select(WorkerLease).where(
            WorkerLease.workspace_id == workspace_id,
            WorkerLease.job_type == JobType.TEAM_EXECUTION_LOOP.value,
            WorkerLease.resource_id == team_id,
        )
        leases = self._session.scalars(statement).all()
        events: list[TimelineEvent] = []
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
            if within(lease.started_at, filters):
                events.append(
                    TimelineEvent(
                        id=f"worker_lease:{lease.id}:started",
                        source_type="worker_lease",
                        event_type="worker_lease.started",
                        occurred_at=aware_datetime(lease.started_at),
                        resource_id=str(lease.id),
                        message=f"Worker lease started on {lease.worker_id}",
                        metadata=metadata | {"status": lease.status},
                    )
                )
            if lease.finished_at is not None and within(lease.finished_at, filters):
                events.append(
                    TimelineEvent(
                        id=f"worker_lease:{lease.id}:finished",
                        source_type="worker_lease",
                        event_type=f"worker_lease.{lease.status}",
                        occurred_at=aware_datetime(lease.finished_at),
                        resource_id=str(lease.id),
                        message=f"Worker lease {lease.status} on {lease.worker_id}",
                        metadata=metadata | {"status": lease.status},
                    )
                )
        return events

    def _blocked_steps(
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        statement = (
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.agent_team_id == team_id,
            )
        )
        statement = apply_time_filters(statement, TaskStep.updated_at, filters)
        rows = self._session.execute(statement).all()
        events: list[TimelineEvent] = []
        for step, task in rows:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            reason = dependencies.get("blocked_reason")
            reason_text = reason if isinstance(reason, str) and reason else "unknown"
            events.append(
                TimelineEvent(
                    id=f"task_step:{step.id}:scheduling_blocked",
                    source_type="task_step",
                    event_type="team.runtime.step.scheduling_blocked",
                    occurred_at=aware_datetime(step.updated_at),
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

    def _mcp_governance_events(
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        server_ids = self._team_mcp_server_ids(workspace_id, team_id)
        if not server_ids:
            return []
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.target_type == "mcp_server",
            AuditEvent.target_id.in_({str(server_id) for server_id in server_ids}),
            AuditEvent.action.in_(
                {
                    "capability_governance.mcp_health_check_refreshed",
                    "capability_governance.mcp_server_disabled",
                }
            ),
        )
        statement = apply_time_filters(statement, AuditEvent.created_at, filters)
        audit_events = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"mcp_governance:{event.id}",
                source_type="mcp_governance",
                event_type=event.action,
                occurred_at=aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=mcp_governance_message(event),
                metadata={
                    "audit_event_id": str(event.id),
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "user_id": str(event.user_id) if event.user_id is not None else None,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "audit_metadata": dict(event.audit_metadata or {}),
                },
            )
            for event in audit_events
        ]

    def _team_mcp_server_ids(self, workspace_id: UUID, team_id: UUID) -> set[UUID]:
        configured_tools = self._teamconfigured_mcp_tools(workspace_id, team_id)
        if not configured_tools:
            return set()
        statement = (
            select(McpToolAllowlist.mcp_server_id)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.status == "active",
                McpServer.workspace_id == workspace_id,
            )
        )
        if "*" not in configured_tools:
            statement = statement.where(McpToolAllowlist.tool_name.in_(configured_tools))
        return set(self._session.scalars(statement).all())

    def _teamconfigured_mcp_tools(self, workspace_id: UUID, team_id: UUID) -> set[str]:
        rows = self._session.scalars(
            select(AgentProfile.tool_policy)
            .join(AgentTeamMember, AgentTeamMember.agent_profile_id == AgentProfile.id)
            .where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
                AgentTeamMember.status == "active",
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.status == "active",
            )
        ).all()
        configured: set[str] = set()
        for policy in rows:
            configured.update(configured_mcp_tools(policy))
        return configured

    def _runtime_events(
        self, workspace_id: UUID, runtime_ids: set[UUID], filters: TimelineFilters
    ) -> list[TimelineEvent]:
        if not runtime_ids:
            return []
        statement = select(RuntimeEvent).where(
            RuntimeEvent.workspace_id == workspace_id,
            RuntimeEvent.workspace_runtime_id.in_(runtime_ids),
        )
        statement = apply_time_filters(statement, RuntimeEvent.created_at, filters)
        runtime_events = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"runtime_event:{event.id}",
                source_type="runtime_event",
                event_type=event.event_type,
                occurred_at=aware_datetime(event.created_at),
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
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> list[TimelineEvent]:
        run_ids = self._team_run_event_ids(workspace_id, team_id)
        if not run_ids:
            return []
        events_statement = select(RunEvent).where(
            RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids)
        )
        events_statement = apply_time_filters(events_statement, RunEvent.created_at, filters)
        run_events = self._session.scalars(events_statement).all()
        return [
            TimelineEvent(
                id=f"run_event:{event.id}",
                source_type="run_event",
                event_type=event.event_type,
                occurred_at=aware_datetime(event.created_at),
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
        self, workspace_id: UUID, team_id: UUID, filters: TimelineFilters, queue: RedisQueue | None
    ) -> list[TimelineEvent]:
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
                TimelineEvent(
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
                        "error": redact_secret_like_text(str(exc)),
                    },
                )
            ]
        events: list[TimelineEvent] = []
        for state, jobs in jobs_by_state.items():
            for job in jobs:
                occurred_at = queue_job_time(job)
                if not within(occurred_at, filters):
                    continue
                events.append(
                    TimelineEvent(
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
                            "last_error": redact_secret_like_text(job.last_error)
                            if job.last_error is not None
                            else None,
                            "last_error_type": job.last_error_type,
                            "last_failed_at": job.last_failed_at.isoformat()
                            if job.last_failed_at is not None
                            else None,
                            "routing": redact_metadata(dict(job.routing)),
                            "trace": redact_metadata(job.trace_metadata()),
                        },
                    )
                )
        return events
