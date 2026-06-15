from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.services.workspace_import_conflicts import (
    _disabled_skill_install_conflict,
    _skip_conflict,
)
from backend.app.api.services.workspace_import_fields import (
    _dict_field,
    _string_field,
    _string_list_field,
)
from backend.app.api.services.workspace_import_resolution import _resolution_action
from backend.app.api.services.workspace_metadata_import_context import (
    WorkspaceMetadataImportContext,
)
from backend.app.capabilities.models import Skill, WorkspaceSkillInstall


class SkillInstallMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_skill_installs(self, ctx: WorkspaceMetadataImportContext) -> None:
        if ctx.request.import_skill_installs:
            for item in ctx.request.export.skill_installs[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                installed_key = _string_field(item, "installed_key")
                if self._skill_install_exists(ctx.workspace.id, installed_key):
                    ctx.skipped_counts["skill_installs"] += 1
                    ctx.conflict_plan.append(
                        _skip_conflict(
                            collection="skill_installs",
                            source_id=source_id,
                            field="installed_key",
                            source_value=installed_key,
                            target_value=installed_key,
                            message=(
                                f"Skill install {installed_key!r} already exists in target "
                                "ctx.workspace."
                            ),
                        )
                    )
                    continue
                if _string_field(item, "status", "active") != "active":
                    if (
                        _resolution_action(ctx.request, "skill_installs", source_id)
                        == "exclude_skill"
                    ):
                        ctx.skipped_counts["skill_installs"] += 1
                        continue
                    ctx.skipped_counts["skill_installs"] += 1
                    ctx.conflict_plan.append(
                        _disabled_skill_install_conflict(
                            source_id=source_id,
                            installed_key=installed_key,
                            status=_string_field(item, "status", "disabled"),
                        )
                    )
                    continue
                ctx.created_counts["skill_installs"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["skill_installs"][source_id] = source_id
                    continue
                skill = Skill(
                    key=f"imported.{ctx.workspace.id}.{installed_key}",
                    name=_string_field(item, "installed_name"),
                    version=_string_field(item, "installed_version"),
                    description=_string_field(item, "installed_description"),
                    capability_keys=_string_list_field(item, "installed_capability_keys"),
                    manifest=_dict_field(item, "installed_manifest"),
                    owner_workspace_id=ctx.workspace.id,
                    visibility="private",
                    status="active",
                )
                self._session.add(skill)
                self._session.flush()
                install = WorkspaceSkillInstall(
                    workspace_id=ctx.workspace.id,
                    skill_id=skill.id,
                    installed_by_user_id=ctx.user_id,
                    installed_key=installed_key,
                    installed_name=_string_field(item, "installed_name"),
                    installed_version=_string_field(item, "installed_version"),
                    installed_description=_string_field(item, "installed_description"),
                    installed_capability_keys=_string_list_field(
                        item,
                        "installed_capability_keys",
                    ),
                    installed_manifest=_dict_field(item, "installed_manifest"),
                    source_owner_workspace_id=None,
                    source_visibility=_string_field(item, "source_visibility", "public"),
                    source_checksum=_string_field(item, "source_checksum"),
                    config=_dict_field(item, "config"),
                    status="active",
                )
                self._session.add(install)
                self._session.flush()
                ctx.id_map["skill_installs"][source_id] = str(install.id)

    def _skill_install_exists(self, workspace_id: UUID, installed_key: str) -> bool:
        return self._session.scalar(
            select(WorkspaceSkillInstall.id).where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.installed_key == installed_key,
            )
        ) is not None
