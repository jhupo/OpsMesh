from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_utils import (
    TEAM_RUNTIME_CAPABILITY_KEY,
    apply_time_filters,
    aware_datetime,
    datetime_from_value,
    within,
)
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam


class TeamRuntimeSchedulerTimelineCollector:
    def __init__(self, session: Session) -> None:
        self._session = session

    def scheduler_scan(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
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

    def blocked_steps(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
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
