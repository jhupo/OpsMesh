"""Public SDK Session protocol delegates persistence to native Session Activities."""

from datetime import timedelta
from typing import cast

from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from opsmesh.agents.execution.providers.openai.activity_policy import (
        AGENT_ACTIVITY_RETRY_POLICY,
    )
    from opsmesh.agents.execution.providers.openai.contracts import AgentSessionReference
    from opsmesh.agents.sessions.contracts import SessionOperation


class TemporalSession:
    session_settings: SessionSettings | None = None

    def __init__(self, reference: AgentSessionReference) -> None:
        self._reference = reference
        self.session_id = reference.session_id

    async def _operate(
        self, operation: SessionOperation
    ) -> list[TResponseInputItem] | TResponseInputItem | None:
        return cast(
            list[TResponseInputItem] | TResponseInputItem | None,
            await workflow.execute_activity(
                "opsmesh.sdk_session",
                operation,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
            ),
        )

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return cast(
            list[TResponseInputItem],
            await self._operate(
                SessionOperation(reference=self._reference, method="get_items", limit=limit)
            ),
        )

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self._operate(
            SessionOperation(
                reference=self._reference,
                method="add_items",
                items=cast(list[dict[str, object]], items),
            )
        )

    async def pop_item(self) -> TResponseInputItem | None:
        return cast(
            TResponseInputItem | None,
            await self._operate(SessionOperation(reference=self._reference, method="pop_item")),
        )

    async def clear_session(self) -> None:
        await self._operate(SessionOperation(reference=self._reference, method="clear_session"))
