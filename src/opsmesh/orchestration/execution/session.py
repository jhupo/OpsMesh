"""Public SDK Session protocol delegated to durable native-history Activities."""

from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.orchestration.sessions.contracts import HistoryMethod, SessionActivityRequest


class TemporalSession:
    session_settings: SessionSettings | None = None

    def __init__(self, *, principal_id: UUID, session_id: UUID, run_id: UUID) -> None:
        self.principal_id = principal_id
        self.session_id = str(session_id)
        self.run_id = run_id

    async def _operate(
        self,
        method: HistoryMethod,
        *,
        items: list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> Any:
        return await workflow.execute_activity(
            "orchestration.session",
            SessionActivityRequest(
                principal_id=self.principal_id,
                session_id=UUID(self.session_id),
                run_id=self.run_id,
                method=method,
                items=items or [],
                limit=limit,
            ),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return cast("list[TResponseInputItem]", await self._operate("get_items", limit=limit))

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self._operate("add_items", items=cast("list[dict[str, Any]]", items))

    async def pop_item(self) -> TResponseInputItem | None:
        return cast("TResponseInputItem | None", await self._operate("pop_item"))

    async def clear_session(self) -> None:
        await self._operate("clear_session")
