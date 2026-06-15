from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.services.workspace_import_conflicts import _skip_conflict
from backend.app.api.services.workspace_import_fields import (
    _dict_field,
    _int_field,
    _remap_agent_skills,
    _string_field,
)
from backend.app.api.services.workspace_import_resolution import _resolved_import_name
from backend.app.api.services.workspace_metadata_import_context import (
    WorkspaceMetadataImportContext,
)


class AgentMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_agents(self, ctx: WorkspaceMetadataImportContext) -> None:
        if ctx.request.import_agents:
            for item in ctx.request.export.agents[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                imported_name = _resolved_import_name(
                    ctx.request,
                    collection="agents",
                    source_id=source_id,
                    fallback=f"{ctx.request.name_prefix}{_string_field(item, 'name')}",
                )
                if self._agent_exists(ctx.workspace.id, imported_name):
                    ctx.skipped_counts["agents"] += 1
                    ctx.conflict_plan.append(
                        _skip_conflict(
                            collection="agents",
                            source_id=source_id,
                            field="name",
                            source_value=_string_field(item, "name"),
                            target_value=imported_name,
                            message=f"Agent {imported_name!r} already exists in target workspace.",
                        )
                    )
                    continue
                ctx.created_counts["agents"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["agents"][source_id] = source_id
                    continue
                agent = AgentProfile(
                    workspace_id=ctx.workspace.id,
                    name=imported_name,
                    role=_string_field(item, "role"),
                    description=_string_field(item, "description"),
                    instructions=_string_field(item, "instructions"),
                    model=_string_field(item, "model", "gpt-4.1"),
                    model_settings=_dict_field(item, "model_settings"),
                    capabilities=_dict_field(item, "capabilities"),
                    skills=_remap_agent_skills(
                        _dict_field(item, "skills"),
                        ctx.id_map["skill_installs"],
                    ),
                    tool_policy=_dict_field(item, "tool_policy"),
                    runtime_policy=_dict_field(item, "runtime_policy"),
                    memory_policy=_dict_field(item, "memory_policy"),
                    approval_policy=_dict_field(item, "approval_policy"),
                    version=_int_field(item, "version", 1),
                    status="active",
                )
                self._session.add(agent)
                self._session.flush()
                ctx.id_map["agents"][source_id] = str(agent.id)

    def _agent_exists(self, workspace_id: UUID, name: str) -> bool:
        return self._session.scalar(
            select(AgentProfile.id).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.name == name,
            )
        ) is not None
