"""Bounded in-memory event queue with a fixed worker pool."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
from anyio.abc import TaskGroup, TaskStatus
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)

logger = logging.getLogger(__name__)


@dataclass
class _ScopeLane:
    key: str
    next_sequence: int = 0
    turn: int = 0
    users: int = 0
    completed: set[int] = field(default_factory=set)
    changed: anyio.Event = field(default_factory=anyio.Event)


@dataclass
class _QueuedItem:
    item: Any
    lane: _ScopeLane | None = None
    sequence: int = 0


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
        scope: Optional conversation key, processed in admission order.
        release: Cleanup for every accepted item, including cancellation.
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
        self._lanes: dict[str, _ScopeLane] = {}
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
            if self._send is None:
                raise RuntimeError("The previous event queue has not stopped")
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
        key = self._scope(item) if self._scope is not None else None
        lane = None
        if key is not None:
            lane = self._lanes.get(key) or _ScopeLane(key)
        queued = _QueuedItem(item, lane, lane.next_sequence if lane else 0)
        try:
            self._send.send_nowait(queued)
        except (anyio.WouldBlock, anyio.ClosedResourceError):
            return False
        if lane is not None:
            lane.next_sequence += 1
            lane.users += 1
            self._lanes[lane.key] = lane
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
                with anyio.fail_after(self._shutdown_timeout, shield=True):
                    await self._finished.wait()
        if receive is not None:
            receive.close()
        self._receive = None
        self._task_group = None
        self._worker_scopes = []

    async def _worker(
        self,
        receive: MemoryObjectReceiveStream[Any],
        *,
        task_status: TaskStatus[anyio.CancelScope] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        try:
            with anyio.CancelScope() as scope:
                task_status.started(scope)
                async for queued in receive:
                    try:
                        await self._wait_for_turn(queued)
                        await self._handle(queued.item)
                    except Exception:
                        logger.exception("Channel event handling failed")
                    finally:
                        await self._finish(queued)
        finally:
            receive.close()
            self._running -= 1
            if self._running == 0:
                try:
                    await self._discard_pending()
                finally:
                    self._finished.set()

    async def _finish(self, queued: _QueuedItem) -> None:
        try:
            if self._release_item is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await self._release_item(queued.item)
                    except Exception:
                        logger.exception("Channel event cleanup failed")
        finally:
            self._complete(queued)

    async def _discard_pending(self) -> None:
        self.stop_accepting()
        receive = self._receive
        if receive is None:
            return
        with anyio.CancelScope(shield=True):
            while True:
                try:
                    queued = receive.receive_nowait()
                except (anyio.EndOfStream, anyio.WouldBlock):
                    return
                await self._finish(queued)

    async def _wait_for_turn(self, queued: _QueuedItem) -> None:
        lane = queued.lane
        if lane is None:
            return
        while queued.sequence != lane.turn:
            await lane.changed.wait()

    def _complete(self, queued: _QueuedItem) -> None:
        lane = queued.lane
        if lane is None:
            return
        lane.completed.add(queued.sequence)
        lane.users -= 1
        # Cancelled waiters cannot let later items overtake an active handler.
        while lane.turn in lane.completed:
            lane.completed.remove(lane.turn)
            lane.turn += 1
        changed, lane.changed = lane.changed, anyio.Event()
        changed.set()
        if lane.users == 0:
            self._lanes.pop(lane.key, None)
