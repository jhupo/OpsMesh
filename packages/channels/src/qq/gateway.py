"""QQ Gateway SDK task with cancellation and transport cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

import aiohttp
import anyio
from qqbot_agent_sdk import QQWebSocket, WSCallbacks

if TYPE_CHECKING:
    from .adapter import QQAdapter
    from .channel import QQChannel

logger = logging.getLogger(__name__)
_INITIAL_RETRY_SECONDS = 5


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
        """Bind native gateway callbacks and track pending event tasks."""
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

    def _create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
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


class QQStream:
    """Run the SDK gateway on the host loop for one connection."""

    def __init__(self, channel: QQChannel) -> None:
        """Bind one connection to its native gateway lifecycle."""
        self.channel = channel
        self.adapter = cast("QQAdapter", channel.adapter)
        self.api = channel.client.api
        self._scope: anyio.CancelScope | None = None
        self._finished = anyio.Event()
        self._stopping = False
        self._session: tuple[str | None, int | None] = (None, None)
        callbacks = WSCallbacks(
            on_message_event=self._message,
            on_connected=self._connected,
            on_disconnected=self._disconnected,
            on_fatal_error=self._failed,
            get_token=self.api.ensure_token_sync,
            get_session=lambda: self._session,
            set_session=self._set_session,
            set_heartbeat_interval=lambda interval: None,
            clear_token=self.api.clear_token,
            fail_pending=lambda reason: None,
            get_gateway_url=self.api.get_gateway_url_sync,
        )
        self.gateway = QQGateway(
            callbacks,
            intents=channel.settings.intents,
            deliver=self._deliver,
            name=channel.self_id,
        )

    def stop(self) -> None:
        """Stop intake and cancel the task owning the native gateway."""
        self._stopping = True
        if self._scope is not None:
            self._scope.cancel()

    async def wait_closed(self) -> None:
        """Wait for SDK tasks and transport cleanup to finish."""
        await self._finished.wait()

    async def run(self) -> None:
        """Borrow the SDK's single-loop API without starting another thread."""
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                if self._stopping:
                    return
                session = aiohttp.ClientSession(trust_env=True)
                try:
                    await self._open(session)
                    if not self._stopping:
                        self.gateway.start_listeners()
                        await self.gateway.wait_closed()
                finally:
                    with anyio.CancelScope(shield=True):
                        try:
                            await self.gateway.stop()
                        finally:
                            await session.close()
        except Exception as error:
            logger.error(
                "QQ connection %s failed (%s)",
                self.channel.self_id,
                type(error).__name__,
            )
            self.channel.mark_failed("QQ SDK connection failed")
        finally:
            self._scope = None
            self.channel.inbound_task_finished()
            self._finished.set()

    async def _open(self, session: aiohttp.ClientSession) -> None:
        """Retry initial discovery; subsequent reconnects belong to the SDK."""
        while not self._stopping:
            try:
                url = await self.api.get_gateway_url()
                if not isinstance(url, str) or not url.startswith("wss://"):
                    raise ValueError("QQ gateway must use WSS")
                await self.gateway.open(url, session)
                return
            except Exception as error:
                logger.warning(
                    "QQ initial connection %s failed (%s)",
                    self.channel.self_id,
                    type(error).__name__,
                )
                await anyio.sleep(_INITIAL_RETRY_SECONDS)

    def _set_session(
        self, session_id: str | None, sequence: int | None
    ) -> None:
        self._session = (session_id, sequence)

    def _connected(self) -> None:
        if not self._stopping:
            self.channel.inbound_task_connected()

    def _disconnected(self) -> None:
        if not self._stopping:
            self.channel.inbound_task_disconnected()

    def _failed(self, code: str, message: str) -> None:
        self._disconnected()
        self.channel.mark_failed("QQ SDK gateway rejected the connection")
        self.stop()

    async def _message(self, event_type: str, data: dict[str, Any]) -> None:
        await self._deliver({"op": 0, "t": event_type, "d": data})

    async def _deliver(self, payload: dict[str, Any]) -> None:
        if not self._stopping:
            try:
                await self.adapter.accept_event(self.channel, payload)
            except Exception as error:
                logger.error(
                    "QQ event admission failed (%s)", type(error).__name__
                )
