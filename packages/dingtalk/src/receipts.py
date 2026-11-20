"""Optional per-reply observations of actual transport operations."""

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

import anyio

from asyncio import current_task

from .operations import OperationError

logger = logging.getLogger(__name__)
DeliveryStatus = Literal[
    "prepared", "written", "confirmed", "failed", "unknown"
]


@dataclass(frozen=True)
class DeliveryReceipt:
    """Keep an operation outcome distinct from logical reply completion."""

    attempt_id: str
    message_id: str
    position: int
    sequence: int
    operation: str
    status: DeliveryStatus
    response: Any = None
    error: str | None = None
    original_type: str | None = None
    actual_type: str | None = None
    offset: int | None = None


@dataclass
class _Operation:
    response: Any = None
    status: DeliveryStatus = "confirmed"


class Delivery:
    """Collect optional receipts; never retry sends or own business storage.

    Args:
        observer: Caller-owned async callback using its own short-lived I/O.
        attempt_id: Stable identifier persisted by the caller before sending.
    """

    def __init__(
        self,
        observer: Callable[[DeliveryReceipt], Awaitable[None]] | None = None,
        *,
        attempt_id: str | None = None,
    ):
        """Associate an optional observer with the current plugin."""
        self.attempt_id = attempt_id or uuid4().hex
        self.observer = observer
        self.owner = current_task()
        self._receipts: list[DeliveryReceipt] = []
        self._sequence = 0

    @property
    def receipts(self) -> tuple[DeliveryReceipt, ...]:
        """Return completed observations in operation order."""
        return tuple(self._receipts)

    def check_owner(self) -> None:
        """Reject reuse from a different plugin invocation owner."""
        if self.owner is not current_task():
            raise PermissionError("Delivery belongs to another plugin")

    async def record(
        self,
        message_id: str,
        status: DeliveryStatus,
        *,
        operation="send",
        position=0,
        response=None,
        error=None,
        original_type=None,
        actual_type=None,
        offset=None,
    ) -> None:
        """Observe within the admission without replaying transport I/O."""
        self._sequence += 1
        receipt = DeliveryReceipt(
            self.attempt_id,
            message_id,
            position,
            self._sequence,
            operation,
            status,
            response,
            error,
            original_type,
            actual_type,
            offset,
        )
        self._receipts.append(receipt)
        if self.observer is not None:
            try:
                with anyio.fail_after(10, shield=True):
                    await self.observer(receipt)
            except Exception:
                logger.exception("Delivery observation failed")

    @asynccontextmanager
    async def operation(
        self,
        message_id: str,
        *,
        kind="send",
        position=0,
        original_type=None,
        actual_type=None,
        offset=None,
    ):
        """Keep uncertain failures alongside previously accepted operations."""
        item = _Operation()
        try:
            yield item
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                await self.record(
                    message_id,
                    "failed"
                    if isinstance(error, OperationError) and error.rejected
                    else "unknown",
                    operation=kind,
                    position=position,
                    error=type(error).__name__,
                    original_type=original_type,
                    actual_type=actual_type,
                    offset=offset,
                )
            raise
        else:
            await self.record(
                message_id,
                item.status,
                operation=kind,
                position=position,
                response=item.response,
                original_type=original_type,
                actual_type=actual_type,
                offset=offset,
            )
