from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.workspace.data_transfer.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportConflict,
    WorkspaceImportRequest,
)
from backend.app.domains.workspace.tenants.models import Workspace


@dataclass(slots=True)
class WorkspaceMetadataImportContext:
    workspace: Workspace
    user_id: UUID
    request: WorkspaceImportRequest
    id_map: dict[str, dict[str, str]]
    created_counts: dict[str, int]
    skipped_counts: dict[str, int]
    warnings: list[str]
    conflict_plan: list[WorkspaceImportConflict]


def _dt(value: datetime) -> str:
    return value.isoformat()


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _string_field(item: dict[str, object], key: str, default: str = "") -> str:
    value = item.get(key, default)
    return value if isinstance(value, str) else default


def _optional_string_field(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _dict_field(item: dict[str, object], key: str) -> dict[str, object]:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _remap_agent_skills(
    skills: dict[str, object],
    skill_install_id_map: dict[str, str],
) -> dict[str, object]:
    if not skill_install_id_map:
        return skills
    remapped = dict(skills)
    values = remapped.get("installed_skill_ids")
    if not isinstance(values, list):
        return remapped
    remapped["installed_skill_ids"] = [
        skill_install_id_map.get(value, value) if isinstance(value, str) else value
        for value in values
    ]
    return remapped


def _optional_dict_field(item: dict[str, object], key: str) -> dict[str, object] | None:
    value = item.get(key)
    return value if isinstance(value, dict) else None


def _string_list_field(item: dict[str, object], key: str) -> list[str]:
    value = item.get(key)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _int_field(item: dict[str, object], key: str, default: int) -> int:
    value = item.get(key, default)
    return value if isinstance(value, int) else default


def _int_from_optional_string(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_field(item: dict[str, object], key: str, default: bool) -> bool:
    value = item.get(key, default)
    return value if isinstance(value, bool) else default


def _uuid_or_none(value: str | None) -> UUID | None:
    if not value:
        return None
    return UUID(value)


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _resolution(
    request: WorkspaceImportRequest | WorkspaceArchiveImportRequest,
    collection: str,
    source_id: str,
) -> dict[str, object]:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    return resolution if isinstance(resolution, dict) else {}


def _resolution_action(
    request: WorkspaceImportRequest | WorkspaceArchiveImportRequest,
    collection: str,
    source_id: str,
) -> str | None:
    action = _resolution(request, collection, source_id).get("action")
    return action if isinstance(action, str) else None


def _archive_resolution_action(
    request: WorkspaceArchiveImportRequest,
    collection: str,
    source_id: str,
) -> str | None:
    return _resolution_action(request, collection, source_id)


def _resolved_import_name(
    request: WorkspaceImportRequest,
    *,
    collection: str,
    source_id: str,
    fallback: str,
) -> str:
    resolution = _resolution(request, collection, source_id)
    if resolution.get("action") != "rename":
        return fallback
    new_name = resolution.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        return fallback
    return new_name.strip()[:160]


def _resolved_runtime_policy(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> dict[str, object]:
    source_policy = _dict_field(item, "policy")
    resolution = _resolution(request, "runtime_spaces", _string_field(item, "id"))
    policy = resolution.get("policy")
    if resolution.get("action") == "add_runtime_policy" and isinstance(policy, dict):
        return policy
    return source_policy


def _resolved_quota_limit(request: WorkspaceImportRequest, item: dict[str, object]) -> int:
    source_id = _string_field(item, "id")
    source_limit = _int_field(item, "limit_value", 0)
    source_reserved = _int_field(item, "reserved_value", 0)
    if _resolution_action(request, "runtime_space_quotas", source_id) != "increase_quota_limit":
        return source_limit
    raw_limit = _resolution(request, "runtime_space_quotas", source_id).get("limit_value")
    if isinstance(raw_limit, int) and raw_limit >= source_reserved:
        return raw_limit
    return source_limit


def _resolved_quota_reserved_for_validation(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> int:
    source_id = _string_field(item, "id")
    if _resolution_action(request, "runtime_space_quotas", source_id) == (
        "release_source_reservations"
    ):
        return 0
    return _int_field(item, "reserved_value", 0)


def resolved_dependency_id(
    session: Session,
    *,
    workspace_id: UUID,
    request: WorkspaceImportRequest | WorkspaceArchiveImportRequest,
    collection: str,
    source_id: str,
    source_dependency_id: str,
    dependency_field: str,
    id_map: dict[str, str],
    model: type[Any],
) -> str | None:
    if not source_dependency_id:
        return None
    mapped_id = id_map.get(source_dependency_id)
    if mapped_id is not None:
        return mapped_id
    resolution = _resolution(request, collection, source_id)
    dependencies = resolution.get("dependencies")
    if resolution.get("action") != "import_dependency" or not isinstance(dependencies, dict):
        return None
    target_id = dependencies.get(dependency_field)
    if not isinstance(target_id, str) or not _is_valid_uuid(target_id):
        return None
    exists = session.scalar(
        select(model.id).where(
            model.workspace_id == workspace_id,
            model.id == UUID(target_id),
        )
    )
    return target_id if exists is not None else None


def remap_task_step_dependencies(
    dependencies: dict[str, object],
    id_map: dict[str, str],
) -> dict[str, object]:
    remapped = dict(dependencies)
    raw_after_step_ids = dependencies.get("after_step_ids")
    if not isinstance(raw_after_step_ids, list):
        if raw_after_step_ids is not None:
            remapped["after_step_ids"] = []
        return remapped
    remapped_ids: list[str] = []
    for source_id in raw_after_step_ids:
        if not isinstance(source_id, str):
            continue
        mapped_id = id_map.get(source_id)
        if mapped_id is not None:
            remapped_ids.append(mapped_id)
    remapped["after_step_ids"] = remapped_ids
    return remapped
