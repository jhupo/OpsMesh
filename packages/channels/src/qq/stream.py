"""Connect the native QQ gateway lifecycle to the framework."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
import anyio
from qqbot_agent_sdk import WSCallbacks

from .gateway import QQGateway

if TYPE_CHECKING:
    from .channel import QQChannel

logger = logging.getLogger(__name__)
_INITIAL_RETRY_SECONDS = 5


class QQStream:
    """Run the SDK on the host loop and expose connection state to channels."""

    def __init__(self, channel: QQChannel, secret: str) -> None:
        self.channel = channel
        self.adapter = channel.adapter
        self.api = self.adapter.client.account(channel, secret)
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
            if self.channel.ready:
                self.adapter.stream_disconnected(self.channel)
            self.adapter.stream_finished(self.channel)
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
            self.adapter.stream_connected(self.channel)

    def _disconnected(self) -> None:
        if not self._stopping:
            self.adapter.stream_disconnected(self.channel)

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
