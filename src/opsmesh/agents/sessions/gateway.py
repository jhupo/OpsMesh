"""Authorize SDK history callbacks without replacing SDK persistence."""

from uuid import UUID

from agents.extensions.memory import SQLAlchemySession
from agents.items import TResponseInputItem
from sqlalchemy import event, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from opsmesh.agents.execution.contracts import AgentSessionBinding
from opsmesh.agents.sessions.models import ACTIVE_SESSION_STATUS, PersistentAgentSession
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDenied
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.db.operations import DatabaseOperations


class AuthorizedSDKSession(SQLAlchemySession):
    """Only workspace authorization and execution ownership extend the native Session."""

    def __init__(self, binding: AgentSessionBinding, database: DatabaseOperations, run_id: UUID):
        self._binding = binding
        self._database = database
        self._run_id = run_id
        url = make_url(binding.database_url)
        if url.get_backend_name() == "sqlite":
            if url.database in {None, "", ":memory:"}:
                raise ValueError("SDK persistence requires a shared database, not in-memory SQLite")
            url = url.set(drivername="sqlite+aiosqlite")
        engine = create_async_engine(url, poolclass=NullPool)
        event.listen(engine.sync_engine, "commit", lambda _: database.guard())
        super().__init__(
            binding.session_id,
            engine=engine,
            create_tables=False,
            sessions_table="sdk_agent_sessions",
            messages_table="sdk_agent_messages",
        )

    async def _authorize(self) -> None:
        def check(session: Session) -> None:
            ExecutionIdentityService(session).for_run(self._binding.workspace_id, self._run_id)
            session_key = session.scalar(
                select(AgentRun.session_key).where(
                    AgentRun.workspace_id == self._binding.workspace_id, AgentRun.id == self._run_id
                )
            )
            if session_key != self.session_id:
                raise ResourceAccessDenied()
            metadata = session.scalar(
                select(PersistentAgentSession).where(
                    PersistentAgentSession.workspace_id == self._binding.workspace_id,
                    PersistentAgentSession.session_key == self.session_id,
                )
            )
            if metadata is None or metadata.status != ACTIVE_SESSION_STATUS:
                raise ResourceAccessDenied()

        await self._database.run(check)
        self._database.guard()

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        await self._authorize()
        return await super().get_items(limit)

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self._authorize()
        await super().add_items(items)

    async def pop_item(self) -> TResponseInputItem | None:
        await self._authorize()
        return await super().pop_item()

    async def clear_session(self) -> None:
        await self._authorize()
        await super().clear_session()

    async def close(self) -> None:
        await self.engine.dispose()
