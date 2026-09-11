from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.service import AgentManagementService
from backend.app.observability.audit_service import AuditService
from backend.app.capabilities.schema_validation import reject_embedded_secrets
from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.runtime_manager.spaces.service import RuntimeSpaceService
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.org_chart import TeamOrgChartBuilder


@dataclass(frozen=True, slots=True)
class TeamCreateCommand:
    name: str
    team_type: str = "delivery"
    description: str = ""
    status: str = "active"
    manager_agent_profile_id: UUID | None = None
    runtime_space_id: UUID | None = None
    coordination_rules: dict[str, object] = field(default_factory=dict)
    default_task_policy: dict[str, object] = field(default_factory=dict)
    capability_policy: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeamUpdateCommand:
    changes: dict[str, object]


@dataclass(frozen=True, slots=True)
class TeamMemberCreateCommand:
    agent_profile_id: UUID
    team_role: str
    department: str | None = None
    position_title: str | None = None
    reports_to_member_id: UUID | None = None
    responsibilities: list[str] = field(default_factory=list)
    skill_weights: dict[str, object] = field(default_factory=dict)
    availability: dict[str, object] = field(default_factory=dict)
    max_concurrent_tasks: int = 1
    accepts_tasks: bool = True
    is_required: bool = False
    order_index: int = 0
    status: str = "active"


@dataclass(frozen=True, slots=True)
class TeamMemberUpdateCommand:
    changes: dict[str, object]


class WorkspaceTeamService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_teams(self, workspace_id: UUID, page: PageParams) -> tuple[list[AgentTeam], int]:
        statement = (
            select(AgentTeam)
            .where(AgentTeam.workspace_id == workspace_id)
            .order_by(AgentTeam.created_at.desc())
        )
        return page_scalars(self._session, statement, page)

    def get_team_org_chart(
        self,
        workspace_id: UUID,
        team_id: UUID,
    ) -> dict[str, object] | None:
        team = self.get_team(workspace_id, team_id)
        if team is None:
            return None
        return TeamOrgChartBuilder(self._session).build(workspace_id, team)

    def create_team(
        self,
        workspace_id: UUID,
        command: TeamCreateCommand,
        actor_user_id: UUID | None = None,
    ) -> AgentTeam:
        if command.runtime_space_id is not None:
            RuntimeSpaceService(self._session).require_runtime_space_for_target(
                workspace_id=workspace_id,
                runtime_space_id=command.runtime_space_id,
                target_type="workspace",
                target_id=workspace_id,
            )
        if command.manager_agent_profile_id is not None:
            self._require_agent(workspace_id, command.manager_agent_profile_id)
        team = AgentTeam(workspace_id=workspace_id, **_team_payload(command))
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

    def update_team(
        self,
        workspace_id: UUID,
        team_id: UUID,
        command: TeamUpdateCommand,
        actor_user_id: UUID | None = None,
    ) -> AgentTeam:
        team = self._session.scalar(
            select(AgentTeam)
            .where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
            .with_for_update()
        )
        if team is None:
            raise ValueError("Team not found")
        changes = dict(command.changes)
        if not changes:
            raise ValueError("At least one team field is required")
        manager_id = changes.get("manager_agent_profile_id")
        if manager_id is not None:
            if not isinstance(manager_id, UUID):
                raise ValueError("Manager agent profile ID must be a UUID")
            self._require_agent(workspace_id, manager_id)
        runtime_space_id = changes.get("runtime_space_id")
        if runtime_space_id is not None:
            if not isinstance(runtime_space_id, UUID):
                raise ValueError("Runtime space ID must be a UUID")
            RuntimeSpaceService(self._session).require_runtime_space_for_target(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                target_type="workspace",
                target_id=workspace_id,
            )
        for key in ("coordination_rules", "default_task_policy"):
            if key in changes:
                value = changes[key]
                if not isinstance(value, dict):
                    raise ValueError(f"{key} must be an object")
                try:
                    reject_embedded_secrets(value, path=key)
                except ValueError as exc:
                    raise ValueError(str(exc)) from exc
        before = _team_snapshot(team, changes)
        for field_name, value in changes.items():
            setattr(team, field_name, value)
        self._session.flush([team])
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.updated",
                target_type="agent_team",
                target_id=team.id,
                metadata={
                    "changed_fields": sorted(changes),
                    "before": before,
                    "after": _team_snapshot(team, changes),
                },
            )
        self._session.commit()
        self._session.refresh(team)
        return team

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
        return page_scalars(self._session, statement, page)

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
        command: TeamMemberCreateCommand,
        actor_user_id: UUID | None = None,
    ) -> AgentTeamMember:
        self._require_team(workspace_id, team_id)
        self._require_agent(workspace_id, command.agent_profile_id)
        if command.reports_to_member_id is not None:
            self._require_team_member(workspace_id, team_id, command.reports_to_member_id)

        member = AgentTeamMember(
            workspace_id=workspace_id,
            agent_team_id=team_id,
            **_team_member_payload(command),
        )
        self._session.add(member)
        self._session.flush()
        self._reject_reporting_cycle(workspace_id, team_id, member.id, member.reports_to_member_id)
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

    def update_team_member(
        self,
        workspace_id: UUID,
        team_id: UUID,
        member_id: UUID,
        command: TeamMemberUpdateCommand,
        actor_user_id: UUID | None = None,
    ) -> AgentTeamMember:
        member = self.get_team_member(workspace_id, team_id, member_id)
        if member is None:
            raise ValueError("Team member not found")
        reports_to_member_id = command.changes.get("reports_to_member_id")
        if reports_to_member_id is not None:
            if not isinstance(reports_to_member_id, UUID):
                raise ValueError("Reporting manager member ID must be a UUID")
            if reports_to_member_id == member.id:
                raise ValueError("Team member cannot report to itself")
            self._require_team_member(workspace_id, team_id, reports_to_member_id)

        if "reports_to_member_id" in command.changes:
            self._reject_reporting_cycle(
                workspace_id,
                team_id,
                member.id,
                reports_to_member_id,
            )
        before = _team_member_update_snapshot(member, command.changes)
        for field_name, value in command.changes.items():
            setattr(member, field_name, value)
        self._session.flush([member])
        if actor_user_id is not None and command.changes:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team_member.updated",
                target_type="agent_team_member",
                target_id=member.id,
                metadata={
                    "agent_team_id": str(team_id),
                    "agent_profile_id": str(member.agent_profile_id),
                    "changed_fields": sorted(command.changes),
                    "before": before,
                    "after": _team_member_update_snapshot(member, command.changes),
                },
            )
        self._session.commit()
        self._session.refresh(member)
        return member

    def _require_team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam:
        team = self.get_team(workspace_id, team_id)
        if team is None:
            raise ValueError("Team not found")
        return team

    def _require_agent(self, workspace_id: UUID, agent_id: UUID) -> None:
        agent = AgentManagementService(self._session).get_agent(workspace_id, agent_id)
        if agent is None:
            raise ValueError("Agent not found")
        if agent.status != "active":
            raise ValueError("Agent is not active")

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

    def _reject_reporting_cycle(
        self,
        workspace_id: UUID,
        team_id: UUID,
        member_id: UUID,
        reports_to_member_id: UUID | None,
    ) -> None:
        if reports_to_member_id is None:
            return
        reports_to_by_id = {
            member.id: member.reports_to_member_id
            for member in self._session.scalars(
                select(AgentTeamMember).where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                )
            ).all()
        }
        reports_to_by_id[member_id] = reports_to_member_id
        seen: set[UUID] = set()
        current: UUID | None = member_id
        while current is not None:
            if current in seen:
                raise ValueError("Team member reporting line cannot contain a cycle")
            seen.add(current)
            current = reports_to_by_id.get(current)


def _team_payload(command: TeamCreateCommand) -> dict[str, object]:
    return {
        "name": command.name,
        "team_type": command.team_type,
        "description": command.description,
        "status": command.status,
        "manager_agent_profile_id": command.manager_agent_profile_id,
        "runtime_space_id": command.runtime_space_id,
        "coordination_rules": command.coordination_rules,
        "default_task_policy": command.default_task_policy,
        "capability_policy": command.capability_policy,
    }


def _team_member_payload(command: TeamMemberCreateCommand) -> dict[str, object]:
    return {
        "agent_profile_id": command.agent_profile_id,
        "team_role": command.team_role,
        "department": command.department,
        "position_title": command.position_title,
        "reports_to_member_id": command.reports_to_member_id,
        "responsibilities": command.responsibilities,
        "skill_weights": command.skill_weights,
        "availability": command.availability,
        "max_concurrent_tasks": command.max_concurrent_tasks,
        "accepts_tasks": command.accepts_tasks,
        "is_required": command.is_required,
        "order_index": command.order_index,
        "status": command.status,
    }


def _team_snapshot(team: AgentTeam, fields: dict[str, object]) -> dict[str, object]:
    return {
        field: (
            str(getattr(team, field))
            if isinstance(getattr(team, field), UUID)
            else getattr(team, field)
        )
        for field in fields
    }


def _team_member_update_snapshot(
    member: AgentTeamMember,
    fields: dict[str, object],
) -> dict[str, object]:
    return {field: _serializable_team_member_value(getattr(member, field)) for field in fields}


def _serializable_team_member_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    return value
