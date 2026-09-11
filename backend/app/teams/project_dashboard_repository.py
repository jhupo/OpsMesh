from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import TaskMessage, TaskStep


class TeamProjectDashboardRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def steps_by_task(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, list[TaskStep]]:
        if not task_ids:
            return {}
        steps = self._session.scalars(
            select(TaskStep)
            .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id.in_(task_ids))
            .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
        ).all()
        grouped: dict[UUID, list[TaskStep]] = defaultdict(list)
        for step in steps:
            grouped[step.task_id].append(step)
        return grouped


    def runs_by_task(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, list[AgentRun]]:
        if not task_ids:
            return {}
        runs = self._session.scalars(
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id.in_(task_ids))
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        ).all()
        grouped: dict[UUID, list[AgentRun]] = defaultdict(list)
        for run in runs:
            if run.task_id is not None:
                grouped[run.task_id].append(run)
        return grouped


    def latest_events(
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


    def artifacts_by_task(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, list[Artifact]]:
        if not task_ids:
            return {}
        artifacts = self._session.scalars(
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id, Artifact.task_id.in_(task_ids))
            .order_by(Artifact.created_at.asc(), Artifact.id.asc())
        ).all()
        grouped: dict[UUID, list[Artifact]] = defaultdict(list)
        for artifact in artifacts:
            if artifact.task_id is not None:
                grouped[artifact.task_id].append(artifact)
        return grouped


    def latest_messages(
        self,
        workspace_id: UUID,
        task_ids: list[UUID],
    ) -> dict[UUID, TaskMessage]:
        if not task_ids:
            return {}
        messages = self._session.scalars(
            select(TaskMessage)
            .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id.in_(task_ids))
            .order_by(TaskMessage.sequence.asc(), TaskMessage.created_at.asc())
        ).all()
        latest: dict[UUID, TaskMessage] = {}
        for message in messages:
            latest[message.task_id] = message
        return latest
