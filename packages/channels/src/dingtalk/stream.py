"""Bind the official DingTalk Stream protocol to the driver lifespan."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anyio
from dingtalk_stream import (
    CallbackHandler,
    CallbackMessage,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
)
from dingtalk_stream.version import VERSION_STRING
from websockets.asyncio.client import connect
from yarl import URL

from .errors import DingTalkProtocolError

if TYPE_CHECKING:
    from .adapter import DingTalkAdapter
    from .channel import DingTalkChannel

logger = logging.getLogger(__name__)
# The SDK logs whole protocol frames. Application logs use safe identifiers.
_protocol_logger = logging.Logger(__name__ + ".protocol")
_protocol_logger.addHandler(logging.NullHandler())
_protocol_logger.propagate = False


class RobotCallback(CallbackHandler):
    """Admit an SDK callback into the framework's bounded event queue."""

    def __init__(self, channel: DingTalkChannel) -> None:
        """Keep the declared channel as the callback's trusted origin."""
        super().__init__()
        self.channel = channel

    async def process(self, message: CallbackMessage) -> tuple[int, str]:
        """Return an SDK acknowledgement after local queue admission."""
        return self.channel.adapter.accept_callback(self.channel, message.data)


class DingTalkStream(DingTalkStreamClient):
    """Own a cancellable connection while retaining SDK parsing and ACKs.

    SDK 0.24.3 start() swallows cancellation and uses an unbounded synchronous
    handshake. This adapter replaces only its network lifecycle; SDK handlers
    and route_message still implement the Stream wire protocol.
    """

    def __init__(self, channel: DingTalkChannel, secret: str) -> None:
        """Configure the native SDK without performing network I/O."""
        super().__init__(
            Credential(channel.settings.client_id, secret),
            logger=_protocol_logger,
        )
        self.channel = channel
        self.adapter: DingTalkAdapter = channel.adapter
        self._scope: anyio.CancelScope | None = None
        self._finished = anyio.Event()
        self._stopping = False
        self.register_callback_handler(
            ChatbotMessage.TOPIC, RobotCallback(channel)
        )

    def stop(self) -> None:
        """Stop receiving without closing the channel's reply client."""
        self._stopping = True
        if self._scope is not None:
            self._scope.cancel()

    async def wait_closed(self) -> None:
        """Wait until the owned connection task releases its socket."""
        await self._finished.wait()

    async def run(self) -> None:
        """Reconnect until stopped; never detach reader or keepalive tasks."""
        self.pre_start()
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                if self._stopping:
                    return
                while True:
                    try:
                        await self._receive()
                    except Exception as error:
                        logger.warning(
                            "DingTalk stream %s interrupted (%s)",
                            self.channel.self_id,
                            type(error).__name__,
                        )
                    finally:
                        if not self._stopping:
                            self.adapter.stream_disconnected(self.channel)
                    await anyio.sleep(self.channel.protocol.reconnect_delay)
        finally:
            self._scope = None
            self._finished.set()
            self.adapter.stream_finished(self.channel)

    async def _receive(self) -> None:
        uri = await self._connection_uri()
        settings = self.channel.protocol
        socket = await connect(
            uri,
            open_timeout=settings.request_timeout,
            close_timeout=settings.shutdown_timeout,
            max_size=1024 * 1024,
            max_queue=16,
            proxy=None,
        )
        self.websocket = socket
        try:
            self.adapter.stream_connected(self.channel)
            async for raw in socket:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise DingTalkProtocolError("Invalid Stream frame")
                if await self.route_message(payload) == self.TAG_DISCONNECT:
                    return
        finally:
            with anyio.move_on_after(
                settings.shutdown_timeout, shield=True
            ) as closing:
                await socket.close()
            if closing.cancel_called:
                socket.transport.abort()
            self.websocket = None

    async def _connection_uri(self) -> str:
        connection = await self.adapter.client.post_json(
            self.OPEN_CONNECTION_API,
            {
                "clientId": self.credential.client_id,
                "clientSecret": self.credential.client_secret,
                "subscriptions": [
                    {"type": "CALLBACK", "topic": ChatbotMessage.TOPIC}
                ],
                "ua": f"dingtalk-sdk-python/v{VERSION_STRING}-union",
                "localIp": "",
            },
        )
        endpoint, ticket = connection.get("endpoint"), connection.get("ticket")
        if not isinstance(endpoint, str) or not isinstance(ticket, str):
            raise DingTalkProtocolError("Invalid Stream connection response")
        uri = URL(endpoint)
        if uri.scheme != "wss" or not uri.host or uri.user or not ticket:
            raise DingTalkProtocolError("Invalid Stream connection endpoint")
        return str(uri.update_query(ticket=ticket))
