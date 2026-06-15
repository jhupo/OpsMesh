from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.execution_overview_constants import (
    ACTIVE_RUN_STATUSES,
    DONE_TASK_STATUSES,
)
from backend.app.teams.models import AgentTeam, AgentTeamMember


class TeamExecutionOverviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def agents(self, workspace_id: UUID, agent_ids: set[UUID]) -> dict[UUID, AgentProfile]:
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def tasks(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool,
    ) -> list[Task]:
        statement = select(Task).where(
            Task.workspace_id == workspace_id,
            Task.agent_team_id == team_id,
        )
        if not include_completed:
            statement = statement.where(~Task.status.in_(DONE_TASK_STATUSES))
        return list(
            self._session.scalars(
                statement.order_by(Task.priority.desc(), Task.updated_at.desc(), Task.id.asc())
            )
        )

    def steps(self, workspace_id: UUID, task_ids: list[UUID]) -> list[TaskStep]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(
                    TaskStep.workspace_id == workspace_id,
                    TaskStep.task_id.in_(task_ids),
                )
                .order_by(TaskStep.order_index.asc(), TaskStep.id.asc())
            )
        )

    def runs(self, workspace_id: UUID, task_ids: list[UUID]) -> list[AgentRun]:
        if not task_ids:
            return []
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.task_id.in_(task_ids),
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.asc())
            )
        )

    def workspace_active_task_ids_by_agent(
        self,
        workspace_id: UUID,
        agent_profile_ids: set[UUID],
    ) -> dict[UUID, set[UUID]]:
        if not agent_profile_ids:
            return {}
        result: dict[UUID, set[UUID]] = defaultdict(set)
        rows = self._session.execute(
            select(AgentRun.agent_profile_id, AgentRun.task_id).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.agent_profile_id.in_(agent_profile_ids),
                AgentRun.task_id.is_not(None),
                AgentRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        ).all()
        for agent_profile_id, task_id in rows:
            if agent_profile_id is None or task_id is None:
                continue
            result[agent_profile_id].add(task_id)
        return dict(result)

    def latest_events(self, workspace_id: UUID, runs: list[AgentRun]) -> dict[UUID, RunEvent]:
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
