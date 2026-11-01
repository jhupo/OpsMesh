"""Dead-letter inspection and explicit audited recovery."""

from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation


class OutboxManagement:
    def __init__(
        self, database: AsyncDatabase, accounts: AccountDirectory, audit: AuditJournal
    ) -> None:
        self.database, self.accounts, self.audit = database, accounts, audit

    async def list_events(
        self, *, actor_id: str, status: str = "dead", limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        await self.accounts.authorize(
            await self.accounts.get_principal(actor_id), "operations:read", "outbox"
        )
        return await self.database.inspect_events(status=status, limit=limit, offset=offset)

    async def requeue(self, event_id: str, *, actor_id: str, reason: str) -> None:
        await self.accounts.authorize(
            await self.accounts.get_principal(actor_id), "operations:manage", "outbox"
        )
        if not reason.strip() or len(reason) > 1000:
            raise OpsMeshError("INVALID_REQUEST", "A bounded recovery reason is required")
        async with audited_operation(
            self.audit, "outbox.requeue", event_id, actor_id, details={"reason": reason}
        ):
            await self.database.requeue_event(event_id)
