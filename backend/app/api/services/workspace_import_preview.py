from collections import Counter

from backend.app.api.schemas.exports import (
    WorkspaceExportResponse,
    WorkspaceImportConflict,
    WorkspaceImportRequiredResolution,
    WorkspaceImportResourcePreview,
    WorkspaceImportResponse,
    WorkspaceImportSuggestedResolution,
)
from backend.app.api.services.workspace_import_fields import _int_from_optional_string


def _populate_import_preview(
    response: WorkspaceImportResponse,
    export: WorkspaceExportResponse,
) -> None:
    conflict_counts = _conflict_counts(response.conflict_plan)
    required_resolutions = _required_resolutions(response.conflict_plan)
    suggested_resolutions = _suggested_resolutions(response.conflict_plan)
    response.required_resolutions = required_resolutions
    response.suggested_resolutions = suggested_resolutions
    required_counts = _conflict_counts(required_resolutions)
    response.resources = [
        WorkspaceImportResourcePreview(
            collection=collection,
            source_count=_source_count(export, collection),
            create_count=response.created_counts.get(collection, 0),
            skip_count=response.skipped_counts.get(collection, 0),
            conflict_count=conflict_counts.get(collection, 0),
            action=_preview_action(
                response.created_counts.get(collection, 0),
                response.skipped_counts.get(collection, 0),
                required_counts.get(collection, 0),
            ),
            required_resolution_count=required_counts.get(collection, 0),
        )
        for collection in _preview_collections(export, response)
    ]
    response.estimated_counts = {
        "source_total": sum(item.source_count for item in response.resources),
        "create_total": sum(item.create_count for item in response.resources),
        "skip_total": sum(item.skip_count for item in response.resources),
        "conflict_total": len(response.conflict_plan),
        "required_resolution_total": len(required_resolutions),
        "suggested_resolution_total": len(suggested_resolutions),
    }


def _import_preview_audit_metadata(response: WorkspaceImportResponse) -> dict[str, object]:
    conflict_counts = _conflict_counts(response.conflict_plan)
    required_counts = _conflict_counts(response.required_resolutions)
    severity_counts = Counter(conflict.severity for conflict in response.conflict_plan)
    strategy_counts = Counter(conflict.strategy for conflict in response.conflict_plan)
    return {
        "source_workspace_id": str(response.source_workspace_id),
        "dry_run": True,
        "created_counts": dict(response.created_counts),
        "skipped_counts": dict(response.skipped_counts),
        "conflict_counts": dict(sorted(conflict_counts.items())),
        "conflict_severity_counts": dict(sorted(severity_counts.items())),
        "conflict_strategy_counts": dict(sorted(strategy_counts.items())),
        "required_resolution_count": len(response.required_resolutions),
        "required_resolution_counts": dict(sorted(required_counts.items())),
        "suggested_resolution_count": len(response.suggested_resolutions),
        "conflict_summaries": [
            {
                "collection": conflict.collection,
                "field": conflict.field,
                "strategy": conflict.strategy,
                "severity": conflict.severity,
            }
            for conflict in response.conflict_plan[:10]
        ],
    }


def _preview_collections(
    export: WorkspaceExportResponse,
    response: WorkspaceImportResponse,
) -> list[str]:
    collections = [
        "manifest",
        "runtime_spaces",
        "runtime_space_quotas",
        "skill_installs",
        "agents",
        "teams",
        "team_members",
        "tasks",
        "task_steps",
        "task_messages",
        "files",
        "artifacts",
    ]
    return [
        collection
        for collection in collections
        if _source_count(export, collection) > 0
        or response.created_counts.get(collection, 0) > 0
        or response.skipped_counts.get(collection, 0) > 0
    ]


def _source_count(export: WorkspaceExportResponse, collection: str) -> int:
    if collection == "manifest":
        return 1
    value = getattr(export, collection, None)
    return len(value) if isinstance(value, list) else 0


def _conflict_counts(
    items: list[WorkspaceImportConflict] | list[WorkspaceImportRequiredResolution],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.collection] = counts.get(item.collection, 0) + 1
    return counts


def _required_resolutions(
    conflicts: list[WorkspaceImportConflict],
) -> list[WorkspaceImportRequiredResolution]:
    return [
        WorkspaceImportRequiredResolution(
            collection=conflict.collection,
            source_id=conflict.source_id,
            field=conflict.field,
            reason=conflict.strategy,
            allowed_actions=_allowed_resolution_actions(conflict),
            message=conflict.message,
        )
        for conflict in conflicts
        if conflict.severity == "error" or conflict.strategy == "reject"
    ]


def _suggested_resolutions(
    conflicts: list[WorkspaceImportConflict],
) -> list[WorkspaceImportSuggestedResolution]:
    return [
        WorkspaceImportSuggestedResolution(
            collection=conflict.collection,
            source_id=conflict.source_id,
            field=conflict.field,
            reason=conflict.strategy,
            allowed_actions=_allowed_resolution_actions(conflict),
            message=conflict.message,
            resolution_key=f"{conflict.collection}:{conflict.source_id}",
            recommended_action=_recommended_resolution_action(conflict),
            resolution_template=_resolution_template(conflict),
        )
        for conflict in conflicts
        if _allowed_resolution_actions(conflict) != ["skip"]
    ]


def _recommended_resolution_action(conflict: WorkspaceImportConflict) -> str:
    if conflict.strategy == "skip_existing" and conflict.field in {"name", "title"}:
        return "rename"
    if conflict.field == "preview_token":
        return "rerun_preview"
    if conflict.field == "checksum_sha256":
        return "replace_archive_object"
    if conflict.field in {"size_bytes", "total_bytes"}:
        return "exclude_object"
    if conflict.field == "format_version":
        return "export_supported_version"
    if conflict.collection == "skill_installs" and conflict.field == "status":
        return "exclude_skill"
    if conflict.collection == "runtime_spaces" and conflict.field == "policy":
        return "add_runtime_policy"
    if conflict.field == "reserved_value":
        return "release_source_reservations"
    if conflict.strategy == "skip_missing_dependency":
        return "import_dependency"
    if conflict.strategy == "reject":
        return "fix_source"
    return "skip"


def _resolution_template(conflict: WorkspaceImportConflict) -> dict[str, object]:
    action = _recommended_resolution_action(conflict)
    if action == "rename":
        return {
            "action": action,
            "new_name": _suggested_rename_value(conflict),
        }
    if action == "add_runtime_policy":
        return {
            "action": action,
            "policy": {"runtime_modes": ["docker"], "network": "restricted"},
        }
    if action == "increase_quota_limit":
        return {
            "action": action,
            "limit_value": _int_from_optional_string(conflict.source_value, 0),
        }
    if action == "import_dependency":
        return {
            "action": action,
            "dependency_field": conflict.field,
            "dependency_id": conflict.source_value,
        }
    if action in {
        "exclude_skill",
        "exclude_runtime_space",
        "exclude_quota",
        "exclude_object",
        "release_source_reservations",
        "replace_archive_object",
        "rerun_preview",
        "export_supported_version",
        "fix_source",
        "skip",
    }:
        return {"action": action}
    return {"action": action}


def _suggested_rename_value(conflict: WorkspaceImportConflict) -> str:
    value = conflict.target_value or conflict.source_value or conflict.source_id
    return f"{value} 2"[:160]


def _allowed_resolution_actions(conflict: WorkspaceImportConflict) -> list[str]:
    if conflict.strategy == "skip_existing" and conflict.field in {"name", "title"}:
        return ["rename", "skip"]
    if conflict.field == "preview_token":
        return ["rerun_preview", "commit_without_token"]
    if conflict.field == "size_bytes":
        return ["increase_max_bytes_per_object", "exclude_object"]
    if conflict.field == "total_bytes":
        return ["increase_max_total_bytes", "exclude_object"]
    if conflict.field == "checksum_sha256":
        return ["replace_archive_object", "exclude_object"]
    if conflict.field == "format_version":
        return ["export_supported_version", "cancel_import"]
    if conflict.collection == "skill_installs" and conflict.field == "status":
        return ["exclude_skill", "enable_in_source_and_reexport"]
    if conflict.collection == "runtime_spaces" and conflict.field == "policy":
        return ["add_runtime_policy", "exclude_runtime_space"]
    if conflict.field == "reserved_value":
        return ["increase_quota_limit", "release_source_reservations", "exclude_quota"]
    if conflict.strategy == "skip_missing_dependency":
        return ["import_dependency", "skip"]
    if conflict.strategy == "reject":
        return ["fix_source", "exclude_object"]
    return ["skip"]


def _preview_action(create_count: int, skip_count: int, required_count: int) -> str:
    if required_count > 0:
        return "requires_resolution"
    if create_count > 0 and skip_count > 0:
        return "partial_import"
    if create_count > 0:
        return "create"
    if skip_count > 0:
        return "skip"
    return "none"


