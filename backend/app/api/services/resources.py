from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agents import AgentProfileCreateRequest
from backend.app.api.schemas.tasks import TaskCreateRequest
from backend.app.api.schemas.teams import AgentTeamCreateRequest, AgentTeamMemberCreateRequest
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.planning.project_plans import ProjectPlanningService
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.snapshots import build_team_snapshot

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

    def create_agent(
        self,
        workspace_id: UUID,
        data: AgentProfileCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentProfile:
        if data.model_provider_credential_id is not None:
            self._require_model_provider_credential(
                workspace_id,
                data.model_provider_credential_id,
            )
        agent = AgentProfile(workspace_id=workspace_id, **data.model_dump())
        self._session.add(agent)
        self._session.flush()
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="agent.created",
                target_type="agent_profile",
                target_id=agent.id,
                metadata={"name": agent.name, "role": agent.role},
            )
        self._session.commit()
        self._session.refresh(agent)
        return agent

    def get_agent(self, workspace_id: UUID, agent_id: UUID) -> AgentProfile | None:
        return self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_id,
            )
        )

    def _require_model_provider_credential(
        self,
        workspace_id: UUID,
        credential_id: UUID,
    ) -> None:
        credential = self._session.scalar(
            select(ModelProviderCredential.id).where(
                ModelProviderCredential.workspace_id == workspace_id,
                ModelProviderCredential.id == credential_id,
                ModelProviderCredential.status == "active",
            )
        )
        if credential is None:
            raise ValueError("Model provider credential not found")

    def list_teams(self, workspace_id: UUID, page: PageParams) -> tuple[list[AgentTeam], int]:
        statement = (
            select(AgentTeam)
            .where(AgentTeam.workspace_id == workspace_id)
            .order_by(AgentTeam.created_at.desc())
        )
        return self._page(statement, page)

    def create_team(
        self,
        workspace_id: UUID,
        data: AgentTeamCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentTeam:
        team = AgentTeam(workspace_id=workspace_id, **data.model_dump())
        self._session.add(team)
        self._session.flush()
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.created",
                target_type="agent_team",
                target_id=team.id,
                metadata={"name": team.name, "team_type": team.team_type},
            )
        self._session.commit()
        self._session.refresh(team)
        return team

    def get_team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
        )

    def list_team_members(
        self,
        workspace_id: UUID,
        team_id: UUID,
        page: PageParams,
    ) -> tuple[list[AgentTeamMember], int]:
        self._require_team(workspace_id, team_id)
        statement = (
            select(AgentTeamMember)
            .where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
        )
        return self._page(statement, page)

    def get_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        member_id: UUID,
    ) -> AgentTeamMember | None:
        return self._session.scalar(
            select(AgentTeamMember).where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
                AgentTeamMember.id == member_id,
            )
        )

    def create_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        data: AgentTeamMemberCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> AgentTeamMember:
        self._require_team(workspace_id, team_id)
        self._require_agent(workspace_id, data.agent_profile_id)
        if data.reports_to_member_id is not None:
            self._require_team_member(workspace_id, team_id, data.reports_to_member_id)

        member = AgentTeamMember(
            workspace_id=workspace_id,
            agent_team_id=team_id,
            **data.model_dump(),
        )
        self._session.add(member)
        self._session.flush()
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team_member.created",
                target_type="agent_team_member",
                target_id=member.id,
                metadata={
                    "agent_team_id": str(team_id),
                    "agent_profile_id": str(member.agent_profile_id),
                    "team_role": member.team_role,
                    "department": member.department,
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

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
        payload = data.model_dump()
        if data.agent_team_id is not None:
            payload["team_snapshot"] = build_team_snapshot(
                self._session,
                workspace_id=workspace_id,
                team_id=data.agent_team_id,
            )
        task = Task(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            **payload,
        )
        self._session.add(task)
        self._session.flush()
        task.project_plan = ProjectPlanningService().create_initial_plan(task)
        RunOrchestrationService(self._session).create_queued_run_for_task(task)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=created_by_user_id,
            action="task.created",
            target_type="task",
            target_id=task.id,
            metadata={"title": task.title, "domain_type": task.domain_type},
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def get_task(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )

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

    def _require_team(self, workspace_id: UUID, team_id: UUID) -> None:
        if self.get_team(workspace_id, team_id) is None:
            raise ValueError("Team not found")

    def _require_agent(self, workspace_id: UUID, agent_id: UUID) -> None:
        if self.get_agent(workspace_id, agent_id) is None:
            raise ValueError("Agent not found")

    def _require_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        team_member_id: UUID,
    ) -> None:
        member = self._session.scalar(
            select(AgentTeamMember.id).where(
                AgentTeamMember.workspace_id == workspace_id,
                AgentTeamMember.agent_team_id == team_id,
                AgentTeamMember.id == team_member_id,
            )
        )
        if member is None:
            raise ValueError("Reporting manager team member not found")
