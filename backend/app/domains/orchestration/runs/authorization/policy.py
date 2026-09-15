from __future__ import annotations

from uuid import UUID

from backend.app.runtime.environment.policies.runtime import policy_disables_network


class RunRuntimeAuthorizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def runtime_resource_grants(
    catalog: dict[str, object] | None,
) -> tuple[set[UUID], set[UUID], set[UUID]]:
    resource_ids: set[UUID] = set()
    runtime_ids: set[UUID] = set()
    runtime_space_ids: set[UUID] = set()
    for resource in _catalog_resources(catalog):
        if resource.get("resource_type") != "runtime" or resource.get("access_mode") != "execute":
            continue
        resource_ids.add(_required_uuid(resource.get("id"), "runtime resource id"))
        locator = resource.get("locator")
        if not isinstance(locator, dict):
            raise RunRuntimeAuthorizationError(
                "runtime_grant_invalid",
                "Effective runtime resource locator is invalid",
            )
        has_runtime = locator.get("workspace_runtime_id") is not None
        has_runtime_space = locator.get("runtime_space_id") is not None
        if has_runtime == has_runtime_space:
            raise RunRuntimeAuthorizationError(
                "runtime_grant_invalid",
                "Runtime resource requires exactly one runtime or runtime space",
            )
        if has_runtime:
            runtime_ids.add(
                _required_uuid(locator.get("workspace_runtime_id"), "workspace runtime id")
            )
        if has_runtime_space:
            runtime_space_ids.add(
                _required_uuid(locator.get("runtime_space_id"), "runtime space id")
            )
    return resource_ids, runtime_ids, runtime_space_ids


def file_resource_ids(catalog: dict[str, object] | None) -> set[UUID]:
    result: set[UUID] = set()
    for resource in _catalog_resources(catalog):
        if (
            resource.get("resource_type") != "file_collection"
            or resource.get("access_mode") != "read"
        ):
            continue
        locator = resource.get("locator")
        if isinstance(locator, dict):
            result.update(_uuid_set(locator.get("file_ids"), field="file resource file_ids"))
    return result


def catalog_has_stdio_tool(catalog: dict[str, object] | None) -> bool:
    raw_tools = catalog.get("tools") if catalog is not None else None
    if not isinstance(raw_tools, list):
        return False
    return any(
        isinstance(item, dict)
        and isinstance(item.get("descriptor"), dict)
        and item["descriptor"].get("source") == "mcp"
        and item["descriptor"].get("mcp_server_type") == "stdio"
        for item in raw_tools
    )


def catalog_for_snapshot(snapshot: dict[str, object]) -> dict[str, object] | None:
    catalog = snapshot.get("capability_catalog")
    return catalog if isinstance(catalog, dict) else None


def snapshot_file_scope_ids(snapshot: dict[str, object]) -> set[UUID]:
    file_scope = snapshot.get("file_scope")
    if not isinstance(file_scope, dict):
        return set()
    return _uuid_set(file_scope.get("allowed_file_ids"), field="snapshot allowed_file_ids")


def runtime_policy_disables_network(policy: dict[str, object]) -> bool:
    network = policy.get("network")
    if isinstance(network, str) and network.lower() in {"none", "disabled", "off"}:
        return True
    if policy_disables_network(policy):
        return True
    mcp_policy = policy.get("mcp")
    if not isinstance(mcp_policy, dict):
        return False
    network_mode = mcp_policy.get("network_mode")
    return isinstance(network_mode, str) and network_mode.lower() in {
        "none",
        "disabled",
        "off",
    }


def _catalog_resources(catalog: dict[str, object] | None) -> tuple[dict[str, object], ...]:
    raw_resources = catalog.get("resources") if catalog is not None else None
    if raw_resources is None:
        return ()
    if not isinstance(raw_resources, list):
        raise RunRuntimeAuthorizationError(
            "runtime_grant_invalid",
            "Effective capability resources are invalid",
        )
    resources: list[dict[str, object]] = []
    for item in raw_resources:
        if not isinstance(item, dict) or not isinstance(item.get("resource"), dict):
            raise RunRuntimeAuthorizationError(
                "runtime_grant_invalid",
                "Effective capability resource entry is invalid",
            )
        resources.append(item["resource"])
    return tuple(resources)


def _uuid_set(value: object, *, field: str) -> set[UUID]:
    if value is None:
        return set()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result = {_required_uuid(item, field) for item in value}
    if len(result) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _required_uuid(value: object, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc
