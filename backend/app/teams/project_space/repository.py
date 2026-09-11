from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.files.models import WorkspaceFile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.execution_overview_constants import DONE_TASK_STATUSES
from backend.app.teams.models import AgentTeam, AgentTeamMember


class TeamProjectSpaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def list_members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                    AgentTeamMember.status == "active",
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            ).all()
        )

    def agents_by_id(self, workspace_id: UUID, agent_ids: list[UUID]) -> dict[UUID, AgentProfile]:
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def list_tasks(
        self,
        workspace_id: UUID,
        team_id: UUID,
        *,
        include_completed: bool,
        limit: int,
    ) -> list[Task]:
        statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
        )
        if not include_completed:
            statement = statement.where(~Task.status.in_(DONE_TASK_STATUSES))
        return list(
            self._session.scalars(
                statement.order_by(
                    Task.priority.desc(),
                    Task.updated_at.desc(),
                    Task.id.asc(),
                ).limit(limit)
            ).all()
        )

    def list_steps(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskStep]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id.in_(task_ids))
                .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
            ).all()
        )

    def list_runs(self, workspace_id: UUID, task_ids: list[UUID]) -> list[AgentRun]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id.in_(task_ids))
                .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
            ).all()
        )

    def list_artifacts(self, workspace_id: UUID, task_ids: list[UUID]) -> list[Artifact]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id.in_(task_ids))
                .order_by(Artifact.created_at.asc(), Artifact.id.asc())
            ).all()
        )

    def list_messages(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskMessage]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id.in_(task_ids))
                .order_by(TaskMessage.sequence.asc(), TaskMessage.id.asc())
            ).all()
        )

    def list_runtime_spaces(
        self,
        workspace_id: UUID,
        runtime_space_ids: set[UUID],
    ) -> list[RuntimeSpace]:
        if not runtime_space_ids:
            return []
        return list(
            self._session.scalars(
                select(RuntimeSpace)
                .where(
                    RuntimeSpace.workspace_id == workspace_id,
                    RuntimeSpace.id.in_(runtime_space_ids),
                )
                .order_by(RuntimeSpace.scope.asc(), RuntimeSpace.name.asc(), RuntimeSpace.id.asc())
            ).all()
        )

    def quotas_by_space(
        self,
        workspace_id: UUID,
        runtime_space_ids: set[UUID],
    ) -> dict[UUID, list[RuntimeSpaceQuota]]:
        grouped: dict[UUID, list[RuntimeSpaceQuota]] = defaultdict(list)
        if not runtime_space_ids:
            return grouped
        quotas = self._session.scalars(
            select(RuntimeSpaceQuota)
            .where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id.in_(runtime_space_ids),
                RuntimeSpaceQuota.status == "active",
            )
            .order_by(RuntimeSpaceQuota.quota_key.asc(), RuntimeSpaceQuota.id.asc())
        ).all()
        for quota in quotas:
            grouped[quota.runtime_space_id].append(quota)
        return grouped

    def reservations_by_space(
        self,
        workspace_id: UUID,
        runtime_space_ids: set[UUID],
    ) -> dict[UUID, list[RuntimeSpaceReservation]]:
        grouped: dict[UUID, list[RuntimeSpaceReservation]] = defaultdict(list)
        if not runtime_space_ids:
            return grouped
        reservations = self._session.scalars(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id.in_(runtime_space_ids),
            )
            .order_by(RuntimeSpaceReservation.created_at.desc(), RuntimeSpaceReservation.id.asc())
        ).all()
        for reservation in reservations:
            grouped[reservation.runtime_space_id].append(reservation)
        return grouped

    def events_by_space(
        self,
        workspace_id: UUID,
        runtime_space_ids: set[UUID],
    ) -> dict[UUID, list[RuntimeSpaceEvent]]:
        grouped: dict[UUID, list[RuntimeSpaceEvent]] = defaultdict(list)
        if not runtime_space_ids:
            return grouped
        events = self._session.scalars(
            select(RuntimeSpaceEvent)
            .where(
                RuntimeSpaceEvent.workspace_id == workspace_id,
                RuntimeSpaceEvent.runtime_space_id.in_(runtime_space_ids),
            )
            .order_by(RuntimeSpaceEvent.created_at.desc(), RuntimeSpaceEvent.id.asc())
            .limit(200)
        ).all()
        for event in events:
            grouped[event.runtime_space_id].append(event)
        return grouped

    def list_active_files(self, workspace_id: UUID) -> list[WorkspaceFile]:
        return list(
            self._session.scalars(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == workspace_id, WorkspaceFile.status == "active")
                .order_by(WorkspaceFile.updated_at.desc(), WorkspaceFile.id.asc())
            ).all()
        )

    def list_active_memory_entries(self, workspace_id: UUID) -> list[WorkspaceMemoryEntry]:
        return list(
            self._session.scalars(
                select(WorkspaceMemoryEntry)
                .where(
                    WorkspaceMemoryEntry.workspace_id == workspace_id,
                    WorkspaceMemoryEntry.status == "active",
                    WorkspaceMemoryEntry.memory_layer.in_(("episodic", "semantic")),
                )
                .order_by(WorkspaceMemoryEntry.importance.desc(), WorkspaceMemoryEntry.id.asc())
            ).all()
        )
