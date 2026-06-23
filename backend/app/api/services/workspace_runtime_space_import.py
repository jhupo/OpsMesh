from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.services.workspace_import_conflicts import (
    _missing_dependency_conflict,
    _missing_runtime_policy_conflict,
    _quota_violation_conflict,
    _skip_conflict,
)
from backend.app.api.services.workspace_import_fields import _dict_field, _string_field
from backend.app.api.services.workspace_import_resolution import (
    _resolution_action,
    _resolved_import_name,
    _resolved_quota_limit,
    _resolved_quota_reserved_for_validation,
    _resolved_runtime_policy,
)
from backend.app.api.services.workspace_metadata_import_context import (
    WorkspaceMetadataImportContext,
)
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota


class RuntimeSpaceMetadataImporter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_runtime_spaces(self, ctx: WorkspaceMetadataImportContext) -> None:
        if ctx.request.import_runtime_spaces:
            for item in ctx.request.export.runtime_spaces[: ctx.request.max_items_per_collection]:
                source_id = _string_field(item, "id")
                if _resolution_action(
                    ctx.request,
                    "runtime_spaces",
                    source_id,
                ) == "exclude_runtime_space":
                    ctx.skipped_counts["runtime_spaces"] += 1
                    continue
                imported_name = _resolved_import_name(
                    ctx.request,
                    collection="runtime_spaces",
                    source_id=source_id,
                    fallback=f"{ctx.request.name_prefix}{_string_field(item, 'name')}",
                )
                if self._runtime_space_exists(ctx.workspace.id, imported_name):
                    ctx.skipped_counts["runtime_spaces"] += 1
                    ctx.conflict_plan.append(
                        _skip_conflict(
                            collection="runtime_spaces",
                            source_id=source_id,
                            field="name",
                            source_value=_string_field(item, "name"),
                            target_value=imported_name,
                            message=(
                                f"Runtime space {imported_name!r} already exists in target "
                                "ctx.workspace."
                            ),
                        )
                    )
                    continue
                runtime_policy = _resolved_runtime_policy(ctx.request, item)
                if not runtime_policy:
                    ctx.skipped_counts["runtime_spaces"] += 1
                    ctx.conflict_plan.append(
                        _missing_runtime_policy_conflict(
                            source_id=source_id,
                            runtime_space_name=_string_field(item, "name"),
                        )
                    )
                    continue
                ctx.created_counts["runtime_spaces"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["runtime_spaces"][source_id] = source_id
                    continue
                runtime_space = RuntimeSpace(
                    workspace_id=ctx.workspace.id,
                    created_by_user_id=ctx.user_id,
                    default_runtime_template_id=None,
                    name=imported_name,
                    scope=_string_field(item, "scope", "ctx.workspace"),
                    status="active",
                    policy=runtime_policy,
                    network_policy=_dict_field(item, "network_policy"),
                    storage_policy=_dict_field(item, "storage_policy"),
                    cleanup_policy=_dict_field(item, "cleanup_policy"),
                )
                self._session.add(runtime_space)
                self._session.flush()
                ctx.id_map["runtime_spaces"][source_id] = str(runtime_space.id)

            for item in ctx.request.export.runtime_space_quotas[
                : ctx.request.max_items_per_collection
            ]:
                source_id = _string_field(item, "id")
                if _resolution_action(
                    ctx.request,
                    "runtime_space_quotas",
                    source_id,
                ) == "exclude_quota":
                    ctx.skipped_counts["runtime_space_quotas"] += 1
                    continue
                runtime_space_id = ctx.id_map["runtime_spaces"].get(
                    _string_field(item, "runtime_space_id")
                )
                if runtime_space_id is None:
                    ctx.skipped_counts["runtime_space_quotas"] += 1
                    ctx.warnings.append("Skipped runtime space quota with missing imported space")
                    ctx.conflict_plan.append(
                        _missing_dependency_conflict(
                            collection="runtime_space_quotas",
                            source_id=source_id,
                            dependency="runtime_space",
                            dependency_id=_string_field(item, "runtime_space_id"),
                        )
                    )
                    continue
                quota_limit = _resolved_quota_limit(ctx.request, item)
                quota_reserved = _resolved_quota_reserved_for_validation(ctx.request, item)
                if quota_reserved > quota_limit:
                    ctx.skipped_counts["runtime_space_quotas"] += 1
                    ctx.conflict_plan.append(
                        _quota_violation_conflict(
                            collection="runtime_space_quotas",
                            source_id=source_id,
                            quota_key=_string_field(item, "quota_key"),
                            limit_value=quota_limit,
                            reserved_value=quota_reserved,
                        )
                    )
                    continue
                ctx.created_counts["runtime_space_quotas"] += 1
                if ctx.request.dry_run:
                    ctx.id_map["runtime_space_quotas"][source_id] = source_id
                    continue
                quota = RuntimeSpaceQuota(
                    workspace_id=ctx.workspace.id,
                    runtime_space_id=UUID(runtime_space_id),
                    quota_key=_string_field(item, "quota_key"),
                    limit_value=quota_limit,
                    reserved_value=0,
                    unit=_string_field(item, "unit", "count"),
                    status="active",
                )
                self._session.add(quota)
                self._session.flush()
                ctx.id_map["runtime_space_quotas"][source_id] = str(quota.id)

    def _runtime_space_exists(self, workspace_id: UUID, name: str) -> bool:
        return self._session.scalar(
            select(RuntimeSpace.id).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.name == name,
            )
        ) is not None
