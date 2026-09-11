from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.core.typing import counts_by_value
from backend.app.runs.activity import run_activity
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep

ACTIVE_RUN_STATUSES = {
    "queued",
    "running",
    "waiting_runtime",
    "waiting_approval",
    "waiting_subworkflow",
}


class TaskLiveStatusService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_status(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_sequence: int = 0,
        message_limit: int = 50,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        steps = self._task_steps(workspace_id, task_id)
        runs = self._task_runs(workspace_id, task_id)
        messages = self._task_messages(
            workspace_id,
            task_id,
            after_sequence=max(after_sequence, 0),
            limit=max(1, min(message_limit, 200)),
        )
        agents = self._agents_by_id(workspace_id, runs, steps, messages)
        latest_events = self._latest_events_by_run_id(workspace_id, runs)
        latest_sequence = self._latest_message_sequence(workspace_id, task_id)
        active_runs = [run for run in runs if run.status in ACTIVE_RUN_STATUSES]

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "task": {
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
                "domain_type": task.domain_type,
                "agent_team_id": task.agent_team_id,
                "runtime_space_id": task.runtime_space_id,
                "completed_at": task.completed_at,
            },
            "summary": {
                "step_status_counts": counts_by_value(step.status for step in steps),
                "run_status_counts": counts_by_value(run.status for run in runs),
                "active_run_phase_counts": counts_by_value(
                    run_activity(run, latest_events.get(run.id))["phase"]
                    for run in active_runs
                ),
                "active_run_count": len(active_runs),
                "latest_message_sequence": latest_sequence,
                "has_more_messages": latest_sequence
                > max((message.sequence for message in messages), default=after_sequence),
                "poll_after_seconds": 1 if active_runs else 5,
            },
            "steps": [
                {
                    "id": step.id,
                    "work_package_id": step.work_package_id,
                    "title": step.title,
                    "status": step.status,
                    "order_index": step.order_index,
                    "assigned_agent_profile_id": step.assigned_agent_profile_id,
                    "assigned_agent": _agent_summary(
                        agents.get(step.assigned_agent_profile_id)
                        if step.assigned_agent_profile_id else None
                    ),
                    "result_summary": step.result_summary,
                }
                for step in steps
            ],
            "active_runs": [
                self._run_payload(
                    run,
                    agents.get(run.agent_profile_id) if run.agent_profile_id else None,
                    latest_events.get(run.id),
                )
                for run in active_runs
            ],
            "recent_messages": [
                {
                    "id": message.id,
                    "sequence": message.sequence,
                    "message_type": message.message_type,
                    "body": message.body,
                    "task_step_id": message.task_step_id,
                    "agent_run_id": message.agent_run_id,
                    "agent_profile_id": message.agent_profile_id,
                    "agent": _agent_summary(
                        agents.get(message.agent_profile_id) if message.agent_profile_id else None
                    ),
                    "payload": message.payload,
                    "created_at": message.created_at,
                }
                for message in messages
            ],
        }

    def _task_steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _task_runs(self, workspace_id: UUID, task_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc())
            )
        )

    def _task_messages(
        self,
        workspace_id: UUID,
        task_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id == task_id,
                    TaskMessage.sequence > after_sequence,
                )
                .order_by(TaskMessage.sequence.asc())
                .limit(limit)
            )
        )

    def _agents_by_id(
        self,
        workspace_id: UUID,
        runs: list[AgentRun],
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            agent_id
            for agent_id in [
                *(run.agent_profile_id for run in runs),
                *(step.assigned_agent_profile_id for step in steps),
                *(message.agent_profile_id for message in messages),
            ]
            if agent_id is not None
        }
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id, AgentProfile.id.in_(agent_ids)
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def _latest_events_by_run_id(
        self,
        workspace_id: UUID,
        runs: list[AgentRun],
    ) -> dict[UUID, RunEvent]:
        run_ids = [run.id for run in runs]
        if not run_ids:
            return {}
        events = self._session.scalars(
            select(RunEvent)
            .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
            .order_by(RunEvent.agent_run_id.asc(), RunEvent.sequence.desc())
        ).all()
        latest: dict[UUID, RunEvent] = {}
        for event in events:
            latest.setdefault(event.agent_run_id, event)
        return latest

    def _latest_message_sequence(self, workspace_id: UUID, task_id: UUID) -> int:
        sequence = self._session.scalar(
            select(TaskMessage.sequence)
            .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
            .order_by(TaskMessage.sequence.desc())
            .limit(1)
        )
        return int(sequence or 0)

    def _run_payload(
        self,
        run: AgentRun,
        agent: AgentProfile | None,
        latest_event: RunEvent | None,
    ) -> dict[str, object]:
        return {
            "id": run.id,
            "status": run.status,
            "task_step_id": run.task_step_id,
            "agent_profile_id": run.agent_profile_id,
            "agent": _agent_summary(agent),
            "runtime_id": run.runtime_id,
            "runtime_space_id": run.runtime_space_id,
            "model": run.model,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "latest_event": _event_summary(latest_event),
            "activity": run_activity(run, latest_event),
        }


def _agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _event_summary(event: RunEvent | None) -> dict[str, object] | None:
    if event is None:
        return None
    return {
        "id": event.id,
        "event_type": event.event_type,
        "sequence": event.sequence,
        "message": event.message,
        "metadata": event.event_metadata,
        "created_at": event.created_at,
    }
