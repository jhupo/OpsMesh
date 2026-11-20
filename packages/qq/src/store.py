import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .event import MessageAuditEvent


class AuditResultStore:
    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future] = {}

    def add_result(self, result: "MessageAuditEvent"):
        audit_id = result.audit_id
        if not audit_id:
            return
        if (future := self._futures.get(audit_id)) is not None and not future.done():
            future.set_result(result)

    async def fetch(
        self, audit_id: str, timeout: float | None = None
    ) -> "MessageAuditEvent":
        if audit_id in self._futures:
            raise RuntimeError("An audit result waiter already exists")
        future = asyncio.get_running_loop().create_future()
        self._futures[audit_id] = future
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            del self._futures[audit_id]

    def close(self) -> None:
        for future in self._futures.values():
            future.cancel()
