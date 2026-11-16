"""Small extensions for events and intents absent from SDK 1.2.2 hooks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from qqbot_agent_sdk import QQWebSocket, WSCallbacks


logger = logging.getLogger(__name__)


class QQGateway(QQWebSocket):
    """Keep native gateway mechanics while exposing complete dispatch frames.

    SDK 1.2.2 filters notices and deduplicates only by message ID. OpsMesh
    admits complete frames instead, retaining stream sequence IDs and letting
    the framework queue decide when delivery has actually been accepted.
    """

    def __init__(
        self,
        callbacks: WSCallbacks,
        *,
        intents: int,
        deliver: Callable[[dict[str, Any]], Awaitable[None]],
        name: str,
    ) -> None:
        super().__init__(callbacks, log_tag=name)
        self._intents = intents
        self._deliver = deliver
        self._frame: dict[str, Any] = {}
        self._pending: set[asyncio.Task[Any]] = set()

    def _dispatch_payload(self, raw: dict[str, Any]) -> None:
        self._frame = raw
        super()._dispatch_payload(raw)

    def _handle_dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        if event_type in {"READY", "RESUMED"}:
            super()._handle_dispatch(event_type, data)
        else:
            self._create_task(self._deliver(dict(self._frame)))

    async def _send_ws_json(self, payload: dict[str, Any], label: str) -> bool:
        if payload.get("op") == 2:
            payload["d"]["intents"] = self._intents
        return await super()._send_ws_json(payload, label)

    def _create_task(
        self, coro: Coroutine[Any, Any, Any]
    ) -> asyncio.Task[Any]:
        """Retain SDK helper tasks so none outlive plugin shutdown."""
        task = asyncio.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._task_finished)
        return task

    def _task_finished(self, task: asyncio.Task[Any]) -> None:
        self._pending.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "QQ SDK helper failed (%s)", type(task.exception()).__name__
            )

    async def _read_events(self) -> None:
        """Treat a clean socket close as a disconnect, not a busy loop."""
        await super()._read_events()
        if self._running:
            raise ConnectionError("QQ gateway closed")

    async def wait_closed(self) -> None:
        """Wait for the SDK listener, including its reconnect loop."""
        if self._listen_task is not None:
            await asyncio.shield(self._listen_task)

    async def stop(self) -> None:
        """Join helper tasks as well as the native listener and heartbeat."""
        try:
            await super().stop()
        finally:
            pending = tuple(self._pending)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._pending.clear()
