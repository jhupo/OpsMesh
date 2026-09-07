from __future__ import annotations

from uuid import UUID

from backend.app.capabilities.schema_validation import reject_embedded_secrets

RESOURCE_ACCESS_MODES = {
    "file_collection": frozenset({"read"}),
    "memory_collection": frozenset({"read", "write", "read_write"}),
    "mcp_resource": frozenset({"read", "execute"}),
    "runtime": frozenset({"execute"}),
    "external_service": frozenset({"execute"}),
}

RESOURCE_LOCATOR_KEYS = {
    "file_collection": frozenset({"file_ids"}),
    "memory_collection": frozenset({"tags", "source_types"}),
    "mcp_resource": frozenset({"mcp_server_id", "credential_reference_id", "uri"}),
    "runtime": frozenset({"runtime_space_id", "workspace_runtime_id"}),
    "external_service": frozenset(
        {"mcp_server_id", "credential_reference_id", "resource_name"}
    ),
}


def normalize_resource_locator(
    resource_type: str,
    access_mode: str,
    locator: dict[str, object],
) -> dict[str, object]:
    allowed_modes = RESOURCE_ACCESS_MODES.get(resource_type)
    if allowed_modes is None:
        raise ValueError(f"Unsupported capability resource type: {resource_type}")
    if access_mode not in allowed_modes:
        raise ValueError(f"Access mode {access_mode} is not allowed for {resource_type}")
    unknown_keys = sorted(set(locator) - RESOURCE_LOCATOR_KEYS[resource_type])
    if unknown_keys:
        raise ValueError(f"Unsupported {resource_type} locator fields: {', '.join(unknown_keys)}")
    reject_embedded_secrets(locator, path="locator")

    if resource_type == "file_collection":
        return {"file_ids": _uuid_list(locator.get("file_ids"), field="locator.file_ids")}
    if resource_type == "memory_collection":
        return {
            key: _string_list(locator.get(key), field=f"locator.{key}")
            for key in ("tags", "source_types")
            if key in locator
        }
    if resource_type == "mcp_resource":
        normalized: dict[str, object] = {
            "mcp_server_id": _uuid(locator.get("mcp_server_id"), field="locator.mcp_server_id"),
            "uri": _non_empty_string(locator.get("uri"), field="locator.uri"),
        }
        if "credential_reference_id" in locator:
            normalized["credential_reference_id"] = _uuid(
                locator.get("credential_reference_id"),
                field="locator.credential_reference_id",
            )
        return normalized
    if resource_type == "runtime":
        provided = [
            key
            for key in ("runtime_space_id", "workspace_runtime_id")
            if locator.get(key) is not None
        ]
        if len(provided) != 1:
            raise ValueError(
                "Runtime locator requires exactly one of runtime_space_id or workspace_runtime_id"
            )
        key = provided[0]
        return {key: _uuid(locator.get(key), field=f"locator.{key}")}
    return {
        "mcp_server_id": _uuid(locator.get("mcp_server_id"), field="locator.mcp_server_id"),
        "credential_reference_id": _uuid(
            locator.get("credential_reference_id"),
            field="locator.credential_reference_id",
        ),
        "resource_name": _non_empty_string(
            locator.get("resource_name"),
            field="locator.resource_name",
        ),
    }


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _uuid_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    normalized = [_uuid(item, field=field) for item in value]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain non-empty strings")
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
