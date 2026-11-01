"""One authorization and audit boundary for capability version availability."""

from collections.abc import Awaitable, Callable
from typing import Any

from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.resource_contracts import ResourceAuthorization, VersionAvailability


async def set_capability_enabled(
    kind: str,
    reference: str,
    enabled: bool,
    *,
    actor_id: str,
    expected_revision: int,
    reason: str,
    authorize: ResourceAuthorization,
    audit: AuditJournal,
    availability: PersistentVersionAvailability,
    read: Callable[[], Awaitable[Any]],
) -> VersionAvailability:
    await authorize(actor_id, "capability:manage", reference)
    await read()
    async with audited_operation(audit, f"{kind}.availability", reference, actor_id):
        return await availability.set_enabled(
            reference,
            enabled,
            actor_id=actor_id,
            expected_revision=expected_revision,
            reason=reason,
        )
