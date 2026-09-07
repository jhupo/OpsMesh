from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.services.workspace_import_conflicts import (
    _missing_dependency_conflict,
    _skip_conflict,
)
from backend.app.api.services.workspace_import_fields import (
    _bool_field,
    _dict_field,
    _int_field,
    _optional_string_field,
    _string_field,
    _string_list_field,
    _uuid_or_none,
)
from backend.app.api.services.workspace_import_resolution import _resolved_import_name
from backend.app.api.services.workspace_metadata_import_context import (
    WorkspaceMetadataImportContext,
)
from backend.app.api.services.workspace_metadata_import_support import resolved_dependency_id
from backend.app.teams.models import AgentTeam, AgentTeamMember


class TeamMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_teams(self, ctx: WorkspaceMetadataImportContext) -> None:
        if ctx.request.import_teams:
            for item in ctx.request.export.teams[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_name = _resolved_import_name(
                    ctx.request,
                    collection="teams",
                    source_id=source_id,
                    fallback=f"{ctx.request.name_prefix}{_string_field(item, 'name')}",
                )
                if self._team_exists(ctx.workspace.id, imported_name):
                    ctx.skipped_counts["teams"] += 1
                    ctx.conflict_plan.append(
                        _skip_conflict(
                            collection="teams",
                            source_id=source_id,
                            field="name",
                            source_value=_string_field(item, "name"),
                            target_value=imported_name,
                            message=f"Team {imported_name!r} already exists in target workspace.",
                        )
                    )
                    continue
                ctx.created_counts["teams"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["teams"][source_id] = source_id
                    continue
                manager_id = ctx.id_map["agents"].get(
                    _string_field(item, "manager_agent_profile_id")
                )
                runtime_space_id = ctx.id_map["runtime_spaces"].get(
                    _string_field(item, "runtime_space_id")
                )
                source_capability_policy = _dict_field(item, "capability_policy")
                if source_capability_policy:
                    ctx.warnings.append(
                        f"Reset capability policy for imported team {imported_name!r}; "
                        "resource grants must be rebound in the target workspace"
                    )
                team = AgentTeam(
                    workspace_id=ctx.workspace.id,
                    name=imported_name,
                    team_type=_string_field(item, "team_type", "general"),
                    description=_string_field(item, "description"),
                    manager_agent_profile_id=_uuid_or_none(manager_id),
                    runtime_space_id=_uuid_or_none(runtime_space_id),
                    coordination_rules=_dict_field(item, "coordination_rules"),
                    default_task_policy=_dict_field(item, "default_task_policy"),
                    capability_policy={},
                    capability_policy_version=1,
                    status="active",
                )
                self._session.add(team)
                self._session.flush()
                ctx.id_map["teams"][source_id] = str(team.id)

            for item in ctx.request.export.team_members[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                team_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="team_members",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "agent_team_id"),
                    dependency_field="agent_team_id",
                    id_map=ctx.id_map["teams"],
                    model=AgentTeam,
                )
                agent_id = resolved_dependency_id(self._session, 
                    workspace_id=ctx.workspace.id,
                    request=ctx.request,
                    collection="team_members",
                    source_id=source_id,
                    source_dependency_id=_string_field(item, "agent_profile_id"),
                    dependency_field="agent_profile_id",
                    id_map=ctx.id_map["agents"],
                    model=AgentProfile,
                )
                reports_to_id = ctx.id_map["team_members"].get(
                    _string_field(item, "reports_to_member_id")
                )
                if team_id is None or agent_id is None:
                    ctx.skipped_counts["team_members"] += 1
                    ctx.warnings.append("Skipped team member with missing imported team or agent")
                    ctx.conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="team_members",
                            source_id=source_id,
                            dependency="team_or_agent",
                            dependency_id=",".join(
                                filter(
                                    None,
                                    [
                                        _string_field(item, "agent_team_id"),
                                        _string_field(item, "agent_profile_id"),
                                    ],
                                )
                            ),
                        )
                    )
                    continue
                ctx.created_counts["team_members"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["team_members"][source_id] = source_id
                    continue
                member = AgentTeamMember(
                    workspace_id=ctx.workspace.id,
                    agent_team_id=UUID(team_id),
                    agent_profile_id=UUID(agent_id),
                    reports_to_member_id=_uuid_or_none(reports_to_id),
                    team_role=_string_field(item, "team_role"),
                    department=_optional_string_field(item, "department"),
                    position_title=_optional_string_field(item, "position_title"),
                    responsibilities=_string_list_field(item, "responsibilities"),
                    skill_weights=_dict_field(item, "skill_weights"),
                    availability=_dict_field(item, "availability"),
                    max_concurrent_tasks=_int_field(item, "max_concurrent_tasks", 1),
                    accepts_tasks=_bool_field(item, "accepts_tasks", True),
                    is_required=_bool_field(item, "is_required", True),
                    order_index=_int_field(item, "order_index", 0),
                    status=_string_field(item, "status", "active"),
                )
                self._session.add(member)
                self._session.flush()
                ctx.id_map["team_members"][source_id] = str(member.id)

    def _team_exists(self, workspace_id: UUID, name: str) -> bool:
        return self._session.scalar(
            select(AgentTeam.id).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.name == name,
            )
        ) is not None
