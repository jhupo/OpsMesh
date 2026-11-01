"""Durable operation intent and outcome shared by platform application services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError

from .api import AuditJournal


@asynccontextmanager
async def audited_operation(
    journal: AuditJournal,
    action: str,
    resource: str,
    actor_id: str,
    *,
    details: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """Fail before mutation if intent cannot persist; never record raw exception text."""
    identity = uuid4().hex
    await journal.record(
        action,
        resource,
        actor_id=actor_id,
        outcome="requested",
        details=details,
        event_id=f"{identity}:intent",
    )
    try:
        yield
    except BaseException as error:
        code = error.code if isinstance(error, OpsMeshError) else type(error).__name__
        await journal.record(
            action,
            resource,
            actor_id=actor_id,
            outcome="failed",
            details={"code": code},
            event_id=f"{identity}:outcome",
        )
        raise
    else:
        await journal.record(
            action,
            resource,
            actor_id=actor_id,
            outcome="succeeded",
            details=details,
            event_id=f"{identity}:outcome",
        )
