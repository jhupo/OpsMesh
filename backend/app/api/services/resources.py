from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import AgentProfileCreateRequest
from backend.app.api.schemas.tasks import TaskCreateRequest
from backend.app.api.schemas.teams import AgentTeamCreateRequest
from backend.app.audit.models import AuditEvent
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam

T = TypeVar("T")


class WorkspaceResourceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_agents(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentProfile], int]:
        statement = select(AgentProfile).where(AgentProfile.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentProfile.status == status)
        return self._page(statement.order_by(AgentProfile.created_at.desc()), page)

    def create_agent(self, workspace_id: UUID, data: AgentProfileCreateRequest) -> AgentProfile:
        agent = AgentProfile(workspace_id=workspace_id, **data.model_dump())
        self._session.add(agent)
        self._session.commit()
        self._session.refresh(agent)
        return agent

    def list_teams(self, workspace_id: UUID, page: PageParams) -> tuple[list[AgentTeam], int]:
        statement = (
            select(AgentTeam)
            .where(AgentTeam.workspace_id == workspace_id)
            .order_by(AgentTeam.created_at.desc())
        )
        return self._page(statement, page)

    def create_team(self, workspace_id: UUID, data: AgentTeamCreateRequest) -> AgentTeam:
        team = AgentTeam(workspace_id=workspace_id, **data.model_dump())
        self._session.add(team)
        self._session.commit()
        self._session.refresh(team)
        return team

    def list_tasks(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[Task], int]:
        statement = select(Task).where(Task.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Task.status == status)
        return self._page(statement.order_by(Task.created_at.desc()), page)

    def create_task(
        self,
        workspace_id: UUID,
        created_by_user_id: UUID,
        data: TaskCreateRequest,
    ) -> Task:
        task = Task(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            **data.model_dump(),
        )
        self._session.add(task)
        self._session.flush()
        RunOrchestrationService(self._session).create_queued_run_for_task(task)
        self._session.commit()
        self._session.refresh(task)
        return task

    def list_runs(
        self,
        workspace_id: UUID,
        page: PageParams,
        status: str | None = None,
    ) -> tuple[list[AgentRun], int]:
        statement = select(AgentRun).where(AgentRun.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        return self._page(statement.order_by(AgentRun.created_at.desc()), page)

    def list_run_events(
        self,
        workspace_id: UUID,
        agent_run_id: UUID,
        page: PageParams,
    ) -> tuple[list[RunEvent], int]:
        statement = (
            select(RunEvent)
            .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id == agent_run_id)
            .order_by(RunEvent.sequence.asc())
        )
        return self._page(statement, page)

    def list_audit_events(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[AuditEvent], int]:
        statement = (
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.created_at.desc())
        )
        return self._page(statement, page)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
