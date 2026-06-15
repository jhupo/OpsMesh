from backend.app.api.schemas.exports import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportRequest,
)
from backend.app.api.services.workspace_import_fields import _dict_field, _int_field, _string_field


def _resolved_import_name(
    request: WorkspaceImportRequest,
    *,
    collection: str,
    source_id: str,
    fallback: str,
) -> str:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    if not isinstance(resolution, dict):
        return fallback
    if resolution.get("action") != "rename":
        return fallback
    new_name = resolution.get("new_name")
    if not isinstance(new_name, str) or not new_name.strip():
        return fallback
    return new_name.strip()[:160]


def _resolution_action(
    request: WorkspaceImportRequest,
    collection: str,
    source_id: str,
) -> str | None:
    resolution = _resolution(request, collection, source_id)
    action = resolution.get("action")
    return action if isinstance(action, str) else None


def _resolution(
    request: WorkspaceImportRequest,
    collection: str,
    source_id: str,
) -> dict[str, object]:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    return resolution if isinstance(resolution, dict) else {}


def _resolved_runtime_policy(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> dict[str, object]:
    source_policy = _dict_field(item, "policy")
    source_id = _string_field(item, "id")
    resolution = _resolution(request, "runtime_spaces", source_id)
    if resolution.get("action") != "add_runtime_policy":
        return source_policy
    policy = resolution.get("policy")
    return policy if isinstance(policy, dict) else source_policy


def _resolved_quota_limit(
    request: WorkspaceImportRequest,
    item: dict[str, object],
) -> int:
    source_id = _string_field(item, "id")
    source_limit = _int_field(item, "limit_value", 0)
    source_reserved = _int_field(item, "reserved_value", 0)
    action = _resolution_action(request, "runtime_space_quotas", source_id)
    if action != "increase_quota_limit":
        return source_limit
    resolution = _resolution(request, "runtime_space_quotas", source_id)
    raw_limit = resolution.get("limit_value")
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


def _archive_resolution_action(
    request: WorkspaceArchiveImportRequest,
    collection: str,
    source_id: str,
) -> str | None:
    resolution = request.resolutions.get(f"{collection}:{source_id}")
    if not isinstance(resolution, dict):
        return None
    action = resolution.get("action")
    return action if isinstance(action, str) else None
