from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from backend.app.agent_runtime.core.contracts import AgentRuntimeResourceGrant

MEMORY_SCOPE_TYPES = frozenset({"workspace", "team", "agent", "task", "run"})
MEMORY_READ_ACCESS_MODES = frozenset({"read", "read_write"})
MEMORY_WRITE_ACCESS_MODES = frozenset({"write", "read_write"})


class MemoryAuthorizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuthorizedMemoryScope:
    resource_id: UUID
    access_mode: str
    source_types: frozenset[str] | None = None
    tags: frozenset[str] | None = None
    scope_types: frozenset[str] | None = None
    scope_ids: frozenset[str] | None = None

    def narrowed_to_source_types(
        self,
        requested: set[str] | None,
    ) -> AuthorizedMemoryScope | None:
        if requested is None:
            return self
        requested_values = frozenset(requested)
        source_types = (
            requested_values
            if self.source_types is None
            else self.source_types & requested_values
        )
        return replace(self, source_types=source_types) if source_types else None

    def allows(
        self,
        *,
        source_type: str,
        tags: set[str],
        scope_type: str,
        scope_id: str,
    ) -> bool:
        return (
            (self.source_types is None or source_type in self.source_types)
            and (self.tags is None or bool(self.tags & tags))
            and (self.scope_types is None or scope_type in self.scope_types)
            and (self.scope_ids is None or scope_id in self.scope_ids)
        )

    def allows_write(
        self,
        *,
        scope_type: str,
        scope_id: str,
        tags: set[str],
        source_type: str = "workspace_memory",
    ) -> bool:
        return (
            (self.source_types is None or source_type in self.source_types)
            and (self.scope_types is None or scope_type in self.scope_types)
            and (self.scope_ids is None or scope_id in self.scope_ids)
            and (self.tags is None or bool(tags) and tags.issubset(self.tags))
        )

    def evidence(self) -> dict[str, object]:
        return {
            "resource_id": str(self.resource_id),
            "access_mode": self.access_mode,
            "source_types": sorted(self.source_types) if self.source_types is not None else None,
            "tags": sorted(self.tags) if self.tags is not None else None,
            "scope_types": sorted(self.scope_types) if self.scope_types is not None else None,
            "scope_ids": sorted(self.scope_ids) if self.scope_ids is not None else None,
        }


def memory_access_scopes(
    grants: tuple[AgentRuntimeResourceGrant, ...],
    *,
    access_modes: frozenset[str],
    requested_source_types: set[str] | None = None,
) -> tuple[AuthorizedMemoryScope, ...]:
    scopes: list[AuthorizedMemoryScope] = []
    for grant in grants:
        if grant.resource_type != "memory_collection" or grant.access_mode not in access_modes:
            continue
        scope_types = _scope_types(grant)
        scope_ids = _locator_values(grant, "scope_ids")
        if scope_ids is not None and (scope_types is None or len(scope_types) != 1):
            raise MemoryAuthorizationError(
                "Memory scope IDs require exactly one memory scope type"
            )
        scope = AuthorizedMemoryScope(
            resource_id=grant.resource_id,
            access_mode=grant.access_mode,
            source_types=_locator_values(grant, "source_types"),
            tags=_locator_values(grant, "tags"),
            scope_types=scope_types,
            scope_ids=scope_ids,
        ).narrowed_to_source_types(requested_source_types)
        if scope is not None:
            scopes.append(scope)
    return tuple(scopes)


def memory_read_scopes(
    grants: tuple[AgentRuntimeResourceGrant, ...],
    *,
    requested_source_types: set[str] | None = None,
) -> tuple[AuthorizedMemoryScope, ...]:
    return memory_access_scopes(
        grants,
        access_modes=MEMORY_READ_ACCESS_MODES,
        requested_source_types=requested_source_types,
    )


def memory_write_scopes(
    grants: tuple[AgentRuntimeResourceGrant, ...],
) -> tuple[AuthorizedMemoryScope, ...]:
    return memory_access_scopes(grants, access_modes=MEMORY_WRITE_ACCESS_MODES)


def _scope_types(grant: AgentRuntimeResourceGrant) -> frozenset[str] | None:
    values = _locator_values(grant, "scope_types")
    if values is not None and not values.issubset(MEMORY_SCOPE_TYPES):
        raise MemoryAuthorizationError("Memory resource contains an unsupported scope type")
    return values


def _locator_values(
    grant: AgentRuntimeResourceGrant,
    key: str,
) -> frozenset[str] | None:
    if key not in grant.locator:
        return None
    value = grant.locator[key]
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise MemoryAuthorizationError(f"Memory resource locator {key} is invalid")
    values = frozenset(value)
    if len(values) != len(value):
        raise MemoryAuthorizationError(f"Memory resource locator {key} contains duplicates")
    return values
