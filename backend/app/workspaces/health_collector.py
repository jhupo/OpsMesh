from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam


@dataclass(frozen=True)
class WorkspaceHealthCollection:
    tasks: list[Task]
    steps: list[TaskStep]
    runs: list[AgentRun]
    artifacts: list[Artifact]
    control_messages: list[TaskMessage]
    team_count: int


class WorkspaceHealthCollector:
    def __init__(self, session: Session) -> None:
        self._session = session

    def collect(self, workspace_id: UUID) -> WorkspaceHealthCollection:
        tasks = self._tasks(workspace_id)
        task_ids = [task.id for task in tasks]
        return WorkspaceHealthCollection(
            tasks=tasks,
            steps=self._steps(workspace_id, task_ids),
            runs=self._runs(workspace_id, task_ids),
            artifacts=self._artifacts(workspace_id, task_ids),
            control_messages=self._control_messages(workspace_id, task_ids),
            team_count=self._team_count(workspace_id),
        )

    def _tasks(self, workspace_id: UUID) -> list[Task]:
        return list(
            self._session.scalars(
                select(Task)
                .where(Task.workspace_id == workspace_id)
                .order_by(Task.updated_at.desc(), Task.id.asc())
            ).all()
        )

    def _team_count(self, workspace_id: UUID) -> int:
        teams = self._session.scalars(
            select(AgentTeam.id).where(AgentTeam.workspace_id == workspace_id)
        ).all()
        return len(teams)

    def _steps(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskStep]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskStep).where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id.in_(task_ids),
                )
            ).all()
        )

    def _runs(self, workspace_id: UUID, task_ids: list[UUID]) -> list[AgentRun]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentRun).where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.task_id.in_(task_ids),
                )
            ).all()
        )

    def _artifacts(self, workspace_id: UUID, task_ids: list[UUID]) -> list[Artifact]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(Artifact).where(
                    Artifact.workspace_id == workspace_id,
                    Artifact.task_id.in_(task_ids),
                )
            ).all()
        )

    def _control_messages(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskMessage]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(
                    TaskMessage.workspace_id == workspace_id,
                    TaskMessage.task_id.in_(task_ids),
                    TaskMessage.message_type.like("task.control.%"),
                )
                .order_by(TaskMessage.created_at.desc(), TaskMessage.id.asc())
            ).all()
        )
