"""SDK history callbacks use independent transactions and return plain history items."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.execution.contracts import AgentRuntimeSessionItem
from backend.app.agents.sessions.models import PersistentAgentSessionRef
from backend.app.agents.sessions.store import SQLAlchemyAgentSession
from backend.app.identity.authorization.execution import ExecutionIdentityService
from backend.app.shared.db.operations import DatabaseOperations


@dataclass
class ScopedAgentSession:
    database: DatabaseOperations
    ref: PersistentAgentSessionRef
    run_id: UUID
    agent_profile_id: UUID | None = None
    agent_team_id: UUID | None = None
    task_id: UUID | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    session_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.session_id = self.ref.session_key

    def _store(self, session: Session) -> SQLAlchemyAgentSession:
        ExecutionIdentityService(session).for_run(self.ref.workspace_id, self.run_id)
        return SQLAlchemyAgentSession(
            db_session=session,
            ref=self.ref,
            agent_profile_id=self.agent_profile_id,
            agent_team_id=self.agent_team_id,
            task_id=self.task_id,
            metadata=self.metadata,
        )

    async def get_items(self, limit: int | None = None) -> list[AgentRuntimeSessionItem]:
        return await self.database.run(
            lambda session: asyncio.run(self._store(session).get_items(limit))
        )

    async def add_items(self, items: list[AgentRuntimeSessionItem]) -> None:
        await self.database.run(lambda session: asyncio.run(self._store(session).add_items(items)))

    async def pop_item(self) -> AgentRuntimeSessionItem | None:
        return await self.database.run(lambda session: asyncio.run(self._store(session).pop_item()))

    async def clear_session(self) -> None:
        await self.database.run(lambda session: asyncio.run(self._store(session).clear_session()))
