"""Shared resource-governance contracts; individual modules retain data ownership."""

from collections.abc import Awaitable, Callable

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract

ResourceAuthorization = Callable[[str, str, str], Awaitable[None]]


class ResourceReference(OpsMeshContract):
    kind: str
    reference: str
    field: str


ResourceReferenceLookup = Callable[[str, str, str], Awaitable[tuple[ResourceReference, ...]]]


class VersionAvailability(OpsMeshContract):
    reference: str
    enabled: bool = True
    revision: int = Field(default=0, ge=0)
    actor_id: str | None = None
    reason: str = Field(default="", max_length=2000)
