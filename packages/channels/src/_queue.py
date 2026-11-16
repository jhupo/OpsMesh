"""Bounded event admission with FIFO ordering within each conversation."""

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
from anyio.abc import TaskGroup, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class _QueuedItem:
    item: Any
    scope: str | None


class EventQueue:
    """A bounded, process-local callback queue; only ready work uses workers.

    Args:
        capacity: Maximum waiting items before admission is rejected.
        concurrency: Maximum simultaneously executing callbacks.
        shutdown_timeout: Seconds to drain before cancelling workers.
        handle: Callback executed for each accepted event.
        scope: Optional conversation key, serialized in admission order.
        release: Cleanup for all admitted events, including discarded work.
    """

    def __init__(
        self,
        *,
        capacity: int,
        concurrency: int,
        shutdown_timeout: float,
        handle: Callable[[Any], Awaitable[None]],
        scope: Callable[[Any], str | None] | None = None,
        release: Callable[[Any], Awaitable[None]] | None = None,
    ) -> None:
        self._capacity = capacity
        self._concurrency = concurrency
        self._shutdown_timeout = shutdown_timeout
        self._handle = handle
        self._scope = scope
        self._release_item = release
        self._pending: deque[_QueuedItem] = deque()
        self._active: set[str] = set()
        self._accepting = False
        self._started = False
        self._changed = anyio.Event()
        self._finished = anyio.Event()
        self._worker_scopes: list[anyio.CancelScope] = []
        self._running = 0

    @property
    def running(self) -> bool:
        """Whether new callbacks can enter the queue."""
        return self._accepting

    def _notify(self) -> None:
        changed, self._changed = self._changed, anyio.Event()
        changed.set()

    async def start(self, task_group: TaskGroup) -> None:
        """Start workers in the host task group without nested lifespans."""
        if self._started:
            if not self._accepting:
                raise RuntimeError("The previous event queue has not stopped")
            return
        self._started = self._accepting = True
        self._finished = anyio.Event()
        self._running = 0
        try:
            for _ in range(self._concurrency):
                self._worker_scopes.append(await task_group.start(self._worker))
        except BaseException:
            self.stop_accepting()
            for scope in self._worker_scopes:
                scope.cancel()
            if not self._running:
                self._finished.set()
            raise

    def put_nowait(self, item: Any) -> bool:
        """Accept without blocking the vendor acknowledgment callback."""
        if not self._accepting or len(self._pending) >= self._capacity:
            return False
        key = self._scope(item) if self._scope is not None else None
        self._pending.append(_QueuedItem(item, key))
        self._notify()
        return True

    def stop_accepting(self) -> None:
        """Reject new work and wake workers to drain accepted callbacks."""
        self._accepting = False
        self._notify()

    async def stop(self) -> None:
        """Drain work, then cancel overdue callbacks and release admission."""
        self.stop_accepting()
        if not self._started:
            return
        with anyio.move_on_after(self._shutdown_timeout) as draining:
            await self._finished.wait()
        if draining.cancelled_caught:
            logger.warning("Channel event queue did not drain in time")
            for scope in self._worker_scopes:
                scope.cancel()
            with anyio.fail_after(self._shutdown_timeout, shield=True):
                await self._finished.wait()
        self._worker_scopes.clear()
        self._started = False

    async def _next(self) -> _QueuedItem | None:
        while True:
            for index, queued in enumerate(self._pending):
                if queued.scope is None or queued.scope not in self._active:
                    del self._pending[index]
                    if queued.scope is not None:
                        self._active.add(queued.scope)
                    return queued
            if not self._accepting and not self._pending:
                return None
            await self._changed.wait()

    async def _worker(
        self,
        *,
        task_status: TaskStatus[anyio.CancelScope] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        self._running += 1
        try:
            with anyio.CancelScope() as scope:
                task_status.started(scope)
                while (queued := await self._next()) is not None:
                    try:
                        await self._handle(queued.item)
                    except Exception:
                        logger.exception("Channel event handling failed")
                    finally:
                        await self._release(queued)
        finally:
            self._running -= 1
            if self._running == 0:
                self.stop_accepting()
                try:
                    while self._pending:
                        await self._release(self._pending.popleft())
                finally:
                    self._finished.set()

    async def _release(self, queued: _QueuedItem) -> None:
        try:
            if self._release_item is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await self._release_item(queued.item)
                    except Exception:
                        logger.exception("Channel event cleanup failed")
        finally:
            if queued.scope is not None:
                self._active.discard(queued.scope)
            self._notify()
