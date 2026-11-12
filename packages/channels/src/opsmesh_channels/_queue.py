"""Bounded in-memory event queue with a fixed worker pool."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from anyio.abc import TaskGroup, TaskStatus
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)

logger = logging.getLogger(__name__)


class EventQueue:
    """Process vendor callbacks after the transport acknowledged them.

    The queue is in memory only. A full queue rejects admission; process
    exits or crashes can lose acknowledged events. Workers
    run in the host task group so they share the driver lifespan instead of
    entering a nested task group from a different scope.

    Args:
        capacity: Maximum queued items before new ones are rejected.
        concurrency: Number of worker tasks consuming the queue.
        shutdown_timeout: Seconds to drain queued items during shutdown.
        handle: Coroutine processing one queued item.
    """

    def __init__(
        self,
        *,
        capacity: int,
        concurrency: int,
        shutdown_timeout: float,
        handle: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._capacity = capacity
        self._concurrency = concurrency
        self._shutdown_timeout = shutdown_timeout
        self._handle = handle
        self._send: MemoryObjectSendStream[Any] | None = None
        self._receive: MemoryObjectReceiveStream[Any] | None = None
        self._task_group: TaskGroup | None = None
        self._worker_scopes: list[anyio.CancelScope] = []
        self._finished = anyio.Event()
        self._running = 0

    @property
    def running(self) -> bool:
        """Whether the queue accepts work."""
        return self._send is not None

    async def start(self, task_group: TaskGroup) -> None:
        """Create the queue and start its workers in the host task group."""
        if self._task_group is not None:
            return
        send, receive = anyio.create_memory_object_stream[Any](self._capacity)
        self._send = send
        self._receive = receive
        self._finished = anyio.Event()
        self._running = self._concurrency
        self._task_group = task_group
        scopes: list[anyio.CancelScope] = []
        try:
            for _ in range(self._concurrency):
                scopes.append(
                    await task_group.start(self._worker, receive.clone())
                )
        except BaseException:
            for scope in scopes:
                scope.cancel()
            self._send = None
            self._receive = None
            self._task_group = None
            send.close()
            receive.close()
            raise
        self._worker_scopes = scopes

    def put_nowait(self, item: Any) -> bool:
        """Enqueue one item and report whether it was accepted."""
        if self._send is None:
            return False
        try:
            self._send.send_nowait(item)
        except (anyio.WouldBlock, anyio.ClosedResourceError):
            return False
        return True

    def stop_accepting(self) -> None:
        """Reject new work while allowing workers to drain accepted items."""
        send, self._send = self._send, None
        if send is not None:
            send.close()

    async def stop(self) -> None:
        """Close the queue, drain workers, then cancel overdue work."""
        self.stop_accepting()
        receive = self._receive
        scopes = self._worker_scopes
        started = self._task_group is not None
        self._receive = None
        self._task_group = None
        self._worker_scopes = []
        if started:
            with anyio.move_on_after(self._shutdown_timeout) as draining:
                await self._finished.wait()
            if draining.cancelled_caught:
                logger.warning(
                    "Channel event queue did not drain within %.1fs",
                    self._shutdown_timeout,
                )
                for scope in scopes:
                    scope.cancel()
                with anyio.move_on_after(self._shutdown_timeout):
                    await self._finished.wait()
        if receive is not None:
            receive.close()

    async def _worker(
        self,
        receive: MemoryObjectReceiveStream[Any],
        *,
        task_status: TaskStatus[anyio.CancelScope] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        try:
            with anyio.CancelScope() as scope:
                task_status.started(scope)
                async for item in receive:
                    try:
                        await self._handle(item)
                    except Exception:
                        logger.exception("Channel event handling failed")
        finally:
            receive.close()
            self._running -= 1
            if self._running == 0:
                self._finished.set()
