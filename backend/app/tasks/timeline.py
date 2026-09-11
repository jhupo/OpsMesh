from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.timeline_events import (
    artifact_timeline_event,
    message_timeline_event,
    run_event_timeline_event,
    run_timeline_events,
    step_timeline_event,
    str_or_none,
    timeline_event_sort_key,
)


class TaskTimelineService:
    """Build a workspace-scoped execution timeline for a task."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_timeline(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        limit: int = 200,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = self._steps(workspace_id, task_id)
        runs = self._runs(workspace_id, task_id)
        run_events = self._run_events(workspace_id, runs)
        messages = self._messages(workspace_id, task_id)
        artifacts = self._artifacts(workspace_id, task_id)
        agents = self._agents(workspace_id, steps, runs, messages, artifacts)
        events = self._events(
            task=task,
            steps=steps,
            runs=runs,
            run_events=run_events,
            messages=messages,
            artifacts=artifacts,
            agents=agents,
        )
        events.sort(key=timeline_event_sort_key)
        truncated_count = max(0, len(events) - limit)
        if truncated_count:
            events = events[-limit:]

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "summary": {
                "task_status": task.status,
                "total_events": len(events) + truncated_count,
                "returned_events": len(events),
                "truncated_count": truncated_count,
                "source_counts": dict(
                    sorted(Counter(event["source_type"] for event in events).items())
                ),
                "phase_counts": dict(sorted(Counter(event["phase"] for event in events).items())),
                "active_run_count": len(
                    [run for run in runs if run.status in {"queued", "running", "waiting_runtime"}]
                ),
                "artifact_count": len(artifacts),
                "correction_count": len(
                    [
                        message
                        for message in messages
                        if message.message_type == "task.correction.created"
                    ]
                ),
            },
            "events": events,
        }

    def _events(
        self,
        *,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        run_events: list[RunEvent],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
        agents: dict[UUID, AgentProfile],
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = [
            {
                "occurred_at": task.created_at,
                "source_type": "task",
                "event_type": "task.created",
                "phase": "intake",
                "status": task.status,
                "title": task.title,
                "summary": "Task created",
                "task_step_id": None,
                "agent_run_id": None,
                "agent_profile_id": None,
                "artifact_id": None,
                "sequence": 0,
                "agent": None,
                "metadata": {
                    "domain_type": task.domain_type,
                    "priority": task.priority,
                    "agent_team_id": str_or_none(task.agent_team_id),
                    "runtime_space_id": str_or_none(task.runtime_space_id),
                    "has_project_plan": task.project_plan is not None,
                    "has_final_output": task.final_output is not None,
                },
            }
        ]
        if task.completed_at is not None:
            events.append(
                {
                    "occurred_at": task.completed_at,
                    "source_type": "task",
                    "event_type": "task.completed",
                    "phase": "review",
                    "status": task.status,
                    "title": task.title,
                    "summary": "Task completed",
                    "task_step_id": None,
                    "agent_run_id": None,
                    "agent_profile_id": None,
                    "artifact_id": None,
                    "sequence": 1,
                    "agent": None,
                    "metadata": {"has_final_output": task.final_output is not None},
                }
            )

        for step in steps:
            events.append(step_timeline_event(step, agents))
        for run in runs:
            events.extend(run_timeline_events(run, agents))
        for event in run_events:
            events.append(run_event_timeline_event(event, runs, agents))
        for message in messages:
            events.append(message_timeline_event(message, agents))
        for artifact in artifacts:
            events.append(artifact_timeline_event(artifact, agents))
        return events

    def _steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _runs(self, workspace_id: UUID, task_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc())
            )
        )

    def _run_events(self, workspace_id: UUID, runs: list[AgentRun]) -> list[RunEvent]:
        run_ids = [run.id for run in runs]
        if not run_ids:
            return []
        return list(
            self._session.scalars(
                select(RunEvent)
                .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
                .order_by(RunEvent.created_at.asc(), RunEvent.sequence.asc())
            )
        )

    def _messages(self, workspace_id: UUID, task_id: UUID) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
                .order_by(TaskMessage.sequence.asc())
            )
        )

    def _artifacts(self, workspace_id: UUID, task_id: UUID) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.asc())
            )
        )

    def _agents(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            agent_id
            for agent_id in (
                [step.assigned_agent_profile_id for step in steps]
                + [run.agent_profile_id for run in runs]
                + [message.agent_profile_id for message in messages]
                + [artifact.agent_profile_id for artifact in artifacts]
            )
            if agent_id is not None
        }
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}
