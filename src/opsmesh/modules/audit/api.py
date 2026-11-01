"""Durable audit facts, independent of sampled traces."""

from datetime import datetime
from typing import Any, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class AuditRecord(OpsMeshContract):
    id: str
    actor_id: str | None
    action: str
    resource: str
    outcome: str
    occurred_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class AuditJournal(Protocol):
    async def record(
        self,
        action: str,
        resource: str,
        *,
        actor_id: str | None = None,
        outcome: str = "succeeded",
        details: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> AuditRecord: ...
    async def query(
        self,
        *,
        actor_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        system_only: bool = False,
        action: str | None = None,
        resource: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditRecord]: ...
