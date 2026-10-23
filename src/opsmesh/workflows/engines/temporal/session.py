"""Native Session operations delegated to authorized Activities."""

from datetime import timedelta
from typing import Any, cast

from agents.items import TResponseInputItem
from agents.memory.session_settings import SessionSettings
from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.execution.contracts import ExecutionIdentity, HistoryCall, SessionReference


class TemporalSession:
    session_settings: SessionSettings | None = None

    def __init__(
        self,
        identity: ExecutionIdentity,
        reference: SessionReference,
        *,
        activity_name: str,
    ) -> None:
        self.identity = identity
        self.reference = reference
        self.activity_name = activity_name
        self.session_id = f"{identity.user_id}:{reference.session_id}:{reference.generation}"

    async def operate(self, request: HistoryCall) -> Any:
        return await workflow.execute_activity(
            self.activity_name,
            request,
            start_to_close_timeout=timedelta(seconds=60),
            # SQLiteSession mutations have no transaction with the PG receipt.
            # A lost acknowledgement must be reconciled, never blindly appended
            # a second time by an Activity retry.
            retry_policy=RetryPolicy(maximum_attempts=1),
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        return cast(
            "list[TResponseInputItem]",
            await self.operate(
                HistoryCall(
                    identity=self.identity,
                    reference=self.reference,
                    method="get_items",
                    limit=limit,
                )
            ),
        )

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        await self.operate(
            HistoryCall(
                identity=self.identity,
                reference=self.reference,
                method="add_items",
                items=cast("list[dict[str, object]]", items),
            )
        )

    async def pop_item(self) -> TResponseInputItem | None:
        return cast(
            "TResponseInputItem | None",
            await self.operate(
                HistoryCall(identity=self.identity, reference=self.reference, method="pop_item")
            ),
        )

    async def clear_session(self) -> None:
        await self.operate(
            HistoryCall(identity=self.identity, reference=self.reference, method="clear_session")
        )
