from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus


@dataclass(slots=True)
class DatabaseRunCancellation:
    """Poll durable run state through a session independent of the active worker unit."""

    bind: Engine | Connection
    workspace_id: UUID
    run_id: UUID
    poll_interval_seconds: float = 0.25
    _cancelled: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    @classmethod
    def for_session(
        cls,
        session: Session,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> DatabaseRunCancellation:
        return cls(
            bind=session.get_bind(),
            workspace_id=workspace_id,
            run_id=run_id,
        )

    async def is_cancelled(self) -> bool:
        if self._cancelled.is_set():
            return True
        factory = sessionmaker(bind=self.bind, autoflush=False, expire_on_commit=False)
        with factory() as session:
            status = session.scalar(
                select(AgentRun.status).where(
                    AgentRun.workspace_id == self.workspace_id,
                    AgentRun.id == self.run_id,
                )
            )
        if status is None or status == RunStatus.CANCELLED.value:
            self._cancelled.set()
            return True
        return False

    async def wait_cancelled(self) -> None:
        while not await self.is_cancelled():
            try:
                await asyncio.wait_for(
                    self._cancelled.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                continue

