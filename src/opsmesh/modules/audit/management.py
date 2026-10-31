"""Authorized audit queries with explicit personal, global and system scopes."""

from datetime import datetime
from typing import Literal

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccountDirectory

from .api import AuditJournal, AuditRecord


class AuditManagement:
    def __init__(self, journal: AuditJournal, accounts: AccountDirectory) -> None:
        self.journal, self.accounts = journal, accounts

    async def query(
        self,
        *,
        actor_id: str,
        scope: Literal["mine", "all", "system"] = "mine",
        subject_id: str | None = None,
        action: str | None = None,
        resource: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditRecord]:
        principal = await self.accounts.get_principal(actor_id)
        if not principal.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        selected = subject_id or actor_id
        if scope != "mine" or selected != actor_id:
            await self.accounts.authorize(
                principal, "audit:read", "*" if scope != "mine" else selected
            )
        return await self.journal.query(
            actor_id=selected if scope == "mine" else None,
            system_only=scope == "system",
            action=action,
            resource=resource,
            outcome=outcome,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
