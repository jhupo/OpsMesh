import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.identity.authorization.resources import ResourceAccessDenied
from backend.app.orchestration.runs.models import AgentRun
from backend.app.runtime.queues.execution_control import current_execution_control
from backend.app.shared.db.operations import DatabaseOperations


@dataclass
class ScopedRunCancellation:
    database: DatabaseOperations
    workspace_id: UUID
    run_id: UUID
    poll_interval_seconds: float = 0.25
    _cancelled: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def _poll(self, session: Session) -> bool:
        status = session.scalar(
            select(AgentRun.status).where(
                AgentRun.workspace_id == self.workspace_id, AgentRun.id == self.run_id
            )
        )
        try:
            ExecutionIdentityService(session).for_run(self.workspace_id, self.run_id)
        except ResourceAccessDenied:
            return True
        return status is None or status == "cancelled"

    async def is_cancelled(self) -> bool:
        control = current_execution_control()
        if control is not None and (
            control.cancel_requested.is_set() or control.ownership_lost.is_set()
        ):
            self._cancelled.set()
        if not self._cancelled.is_set() and await self.database.run(self._poll):
            self._cancelled.set()
        return self._cancelled.is_set()

    async def wait_cancelled(self) -> None:
        while not await self.is_cancelled():
            with suppress(TimeoutError):
                await asyncio.wait_for(self._cancelled.wait(), self.poll_interval_seconds)
