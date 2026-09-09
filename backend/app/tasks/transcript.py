from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskMessage, TaskStep


class TaskInteractionTranscriptService:
    """Build a task-scoped interaction transcript for operator consoles."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_transcript(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        limit: int = 50,
        offset: int = 0,
        message_type: str | None = None,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        base_statement = select(TaskMessage).where(
            TaskMessage.workspace_id == workspace_id,
            TaskMessage.task_id == task_id,
        )
        if message_type is not None:
            base_statement = base_statement.where(TaskMessage.message_type == message_type)

        total = self._session.scalar(
            select(func.count()).select_from(base_statement.order_by(None).subquery())
        )
        messages = list(
            self._session.scalars(
                base_statement.order_by(TaskMessage.sequence.asc()).limit(limit).offset(offset)
            ).all()
        )
        all_type_counts = self._message_type_counts(workspace_id, task_id)
        agent_ids = {message.agent_profile_id for message in messages if message.agent_profile_id}
        step_ids = {message.task_step_id for message in messages if message.task_step_id}
        run_ids = {message.agent_run_id for message in messages if message.agent_run_id}
        agents = self._agents(workspace_id, agent_ids)
        steps = self._steps(workspace_id, task_id, step_ids)
        runs = self._runs(workspace_id, task_id, run_ids)
        latest_sequence = self._latest_sequence(workspace_id, task_id)
        items = [
            _transcript_item(
                message,
                agent=agents.get(message.agent_profile_id) if message.agent_profile_id else None,
                step=steps.get(message.task_step_id) if message.task_step_id else None,
                run=runs.get(message.agent_run_id) if message.agent_run_id else None,
            )
            for message in messages
        ]

        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "summary": {
                "task_status": task.status,
                "total_messages": int(total or 0),
                "returned_messages": len(items),
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(items) < int(total or 0),
                "latest_sequence": latest_sequence,
                "message_type_filter": message_type,
                "message_type_counts": all_type_counts,
            },
            "participants": _participants(messages, agents),
            "items": items,
        }

    def _message_type_counts(self, workspace_id: UUID, task_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(TaskMessage.message_type, func.count())
            .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
            .group_by(TaskMessage.message_type)
        ).all()
        return {str(message_type): int(count or 0) for message_type, count in rows}

    def _latest_sequence(self, workspace_id: UUID, task_id: UUID) -> int:
        value = self._session.scalar(
            select(func.coalesce(func.max(TaskMessage.sequence), 0)).where(
                TaskMessage.workspace_id == workspace_id,
                TaskMessage.task_id == task_id,
            )
        )
        return int(value or 0)

    def _agents(
        self,
        workspace_id: UUID,
        agent_ids: set[UUID],
    ) -> dict[UUID, AgentProfile]:
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def _steps(
        self,
        workspace_id: UUID,
        task_id: UUID,
        step_ids: set[UUID],
    ) -> dict[UUID, TaskStep]:
        if not step_ids:
            return {}
        steps = self._session.scalars(
            select(TaskStep).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id == task_id,
                TaskStep.id.in_(step_ids),
            )
        ).all()
        return {step.id: step for step in steps}

    def _runs(
        self,
        workspace_id: UUID,
        task_id: UUID,
        run_ids: set[UUID],
    ) -> dict[UUID, AgentRun]:
        if not run_ids:
            return {}
        runs = self._session.scalars(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.task_id == task_id,
                AgentRun.id.in_(run_ids),
            )
        ).all()
        return {run.id: run for run in runs}


def _transcript_item(
    message: TaskMessage,
    *,
    agent: AgentProfile | None,
    step: TaskStep | None,
    run: AgentRun | None,
) -> dict[str, object]:
    return {
        "id": message.id,
        "sequence": message.sequence,
        "message_type": message.message_type,
        "phase": _phase(message.message_type),
        "source": _source(message, agent),
        "body": message.body,
        "payload": message.payload,
        "agent": _agent_summary(agent),
        "task_step": _step_summary(step),
        "agent_run": _run_summary(run),
        "created_at": message.created_at,
    }


def _participants(
    messages: list[TaskMessage],
    agents: dict[UUID, AgentProfile],
) -> list[dict[str, object]]:
    counts: dict[UUID, int] = {}
    for message in messages:
        if message.agent_profile_id is None:
            continue
        counts[message.agent_profile_id] = counts.get(message.agent_profile_id, 0) + 1
    return [
        {
            **summary,
            "message_count": counts[agent_id],
        }
        for agent_id, count in sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
        if (summary := _agent_summary(agents.get(agent_id))) is not None
    ]


def _source(message: TaskMessage, agent: AgentProfile | None) -> str:
    if agent is not None:
        if agent.role in {"project_manager", "manager", "pm"}:
            return "manager_agent"
        return "agent"
    if message.message_type.startswith("task.control"):
        return "operator"
    if message.message_type.startswith("task.correction"):
        return "operator"
    if message.message_type.startswith("pm."):
        return "manager"
    return "system"


def _phase(message_type: str) -> str:
    if "." not in message_type:
        return message_type
    return message_type.split(".", 1)[0]


def _agent_summary(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def _step_summary(step: TaskStep | None) -> dict[str, object] | None:
    if step is None:
        return None
    return {
        "id": step.id,
        "work_package_id": step.work_package_id,
        "title": step.title,
        "status": step.status,
        "assigned_agent_profile_id": step.assigned_agent_profile_id,
    }


def _run_summary(run: AgentRun | None) -> dict[str, object] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "model": run.model,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }
