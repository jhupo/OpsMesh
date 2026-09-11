from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.observation_models import TaskObservationRecords


class TaskObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_records(self, *, workspace_id: UUID, task_id: UUID) -> TaskObservationRecords | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None
        steps = self.list_steps(workspace_id, task.id)
        runs = self.list_runs(workspace_id, task.id)
        messages = self.list_messages(workspace_id, task.id)
        artifacts = self.list_artifacts(workspace_id, task.id)
        return TaskObservationRecords(
            task=task,
            steps=steps,
            runs=runs,
            messages=messages,
            artifacts=artifacts,
            run_events=self.list_run_events(workspace_id, runs),
            agents=self.agent_map(workspace_id, steps, runs, messages),
        )

    def list_steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def list_runs(self, workspace_id: UUID, task_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc())
            )
        )

    def list_messages(self, workspace_id: UUID, task_id: UUID) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
                .order_by(TaskMessage.sequence.asc())
            )
        )

    def list_artifacts(self, workspace_id: UUID, task_id: UUID) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.asc())
            )
        )

    def list_run_events(self, workspace_id: UUID, runs: list[AgentRun]) -> list[RunEvent]:
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

    def agent_map(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            item
            for item in (
                [step.assigned_agent_profile_id for step in steps]
                + [run.agent_profile_id for run in runs]
                + [message.agent_profile_id for message in messages]
            )
            if item is not None
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
