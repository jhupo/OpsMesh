"""QQ Gateway WebSocket lifecycle for one bot connection."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import anyio
from websockets.asyncio.client import ClientConnection, connect

from .errors import QQProtocolError

if TYPE_CHECKING:
    from .channel import QQChannel

logger = logging.getLogger(__name__)

_DISPATCH = 0
_HEARTBEAT = 1
_IDENTIFY = 2
_RESUME = 6
_RECONNECT = 7
_INVALID_SESSION = 9
_HELLO = 10
_HEARTBEAT_ACK = 11
_FATAL_CLOSE_CODES = frozenset({4914, 4915})
_MAX_RECONNECT_DELAY = 60.0


class _FatalGatewayClose(QQProtocolError):
    """A Gateway close code that must not be retried by the adapter."""


class QQStream:
    """Own the official QQ Gateway connection for one declared channel."""

    def __init__(self, channel: QQChannel, secret: str) -> None:
        """Prepare a cancellable stream without opening a socket."""
        self.channel = channel
        self.adapter = channel.adapter
        self.secret = secret
        self._scope: anyio.CancelScope | None = None
        self._finished = anyio.Event()
        self._socket: ClientConnection | None = None
        self._stopping = False
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._resume_gateway_url: str | None = None
        self._heartbeat_ack = anyio.Event()
        self._reconnect_attempt = 0

    def stop(self) -> None:
        """Cancel the stream and close its active socket."""
        self._stopping = True
        if self._scope is not None:
            self._scope.cancel()

    async def wait_closed(self) -> None:
        """Wait until the stream task has released its socket."""
        await self._finished.wait()

    async def run(self) -> None:
        """Reconnect after transient Gateway failures until stopped."""
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                while not self._stopping:
                    try:
                        await self._receive()
                    except _FatalGatewayClose as error:
                        logger.error(
                            "QQ Gateway %s stopped permanently (%s)",
                            self.channel.self_id,
                            error,
                        )
                        self._stopping = True
                        self.adapter.stream_disconnected(self.channel)
                    except Exception as error:
                        logger.warning(
                            "QQ Gateway %s interrupted (%s)",
                            self.channel.self_id,
                            type(error).__name__,
                        )
                    finally:
                        if not self._stopping:
                            self.adapter.stream_disconnected(self.channel)
                    if not self._stopping:
                        delay = min(
                            self.channel.protocol.reconnect_delay
                            * (2**self._reconnect_attempt),
                            _MAX_RECONNECT_DELAY,
                        )
                        self._reconnect_attempt += 1
                        await anyio.sleep(delay)
        finally:
            self._scope = None
            self.adapter.stream_finished(self.channel)
            self._finished.set()

    async def _receive(self) -> None:
        settings = self.channel.protocol
        gateway = self._resume_gateway_url
        if gateway is None or self._session_id is None:
            gateway = await self.adapter.client.gateway_url(
                self.channel.self_id,
                self.channel.settings.app_id,
                self.secret,
                sandbox=self.channel.settings.sandbox,
            )
        socket = await connect(
            gateway,
            open_timeout=settings.request_timeout,
            close_timeout=settings.shutdown_timeout,
            max_size=1024 * 1024,
            max_queue=16,
            proxy=None,
        )
        self._socket = socket
        try:
            async for raw in socket:
                frame = self._decode(raw)
                if await self._handle_frame(socket, frame):
                    return
        finally:
            close_code = socket.close_code
            with anyio.move_on_after(
                settings.shutdown_timeout, shield=True
            ) as closing:
                await socket.close()
            if closing.cancel_called:
                socket.transport.abort()
            self._socket = None
            if close_code in _FATAL_CLOSE_CODES:
                raise _FatalGatewayClose(
                    f"QQ Gateway closed with fatal code {close_code}"
                )

    async def _handle_frame(
        self, socket: ClientConnection, frame: dict[str, Any]
    ) -> bool:
        """Handle one Gateway frame and return whether the socket should end."""
        opcode = frame.get("op")
        sequence = frame.get("s")
        if sequence is not None:
            if not isinstance(sequence, int):
                raise QQProtocolError("QQ Gateway sequence is invalid")
            self._sequence = sequence

        if opcode == _HELLO:
            data = frame.get("d")
            if not isinstance(data, dict):
                raise QQProtocolError("QQ Gateway HELLO is invalid")
            interval = data.get("heartbeat_interval")
            if not isinstance(interval, (int, float)) or interval <= 0:
                raise QQProtocolError(
                    "QQ Gateway heartbeat interval is invalid"
                )
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self._heartbeat, socket, interval / 1000)
                await self._authenticate(socket)
                await self._receive_until_closed(socket, tasks)
                tasks.cancel_scope.cancel()
            return True
        if opcode == _HEARTBEAT_ACK:
            return False
        if opcode == _DISPATCH:
            await self._dispatch(frame)
            return False
        if opcode in {_RECONNECT, _INVALID_SESSION}:
            if opcode == _INVALID_SESSION and frame.get("d") is False:
                self._session_id = None
                self._sequence = None
                self._resume_gateway_url = None
            return True
        raise QQProtocolError(f"Unsupported QQ Gateway opcode: {opcode!r}")

    async def _receive_until_closed(
        self, socket: ClientConnection, tasks: anyio.abc.TaskGroup
    ) -> None:
        """Consume post-HELLO frames while the heartbeat task is active."""
        async for raw in socket:
            frame = self._decode(raw)
            opcode = frame.get("op")
            sequence = frame.get("s")
            if sequence is not None:
                if not isinstance(sequence, int):
                    raise QQProtocolError("QQ Gateway sequence is invalid")
                self._sequence = sequence
            if opcode == _DISPATCH:
                await self._dispatch(frame)
            elif opcode == _HEARTBEAT_ACK:
                self._heartbeat_ack.set()
                continue
            elif opcode in {_RECONNECT, _INVALID_SESSION}:
                if opcode == _INVALID_SESSION and frame.get("d") is False:
                    self._session_id = None
                    self._sequence = None
                    self._resume_gateway_url = None
                tasks.cancel_scope.cancel()
                return
            else:
                raise QQProtocolError(
                    f"Unsupported QQ Gateway opcode: {opcode!r}"
                )

    async def _authenticate(self, socket: ClientConnection) -> None:
        """Identify or resume the bot after the Gateway HELLO frame."""
        token = await self.adapter.client.access_token(
            self.channel.self_id,
            self.channel.settings.app_id,
            self.secret,
        )
        if self._session_id is not None and self._sequence is not None:
            payload = {
                "op": _RESUME,
                "d": {
                    "token": f"QQBot {token}",
                    "session_id": self._session_id,
                    "seq": self._sequence,
                },
            }
        else:
            payload = {
                "op": _IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": self.channel.settings.intents,
                    "shard": [0, 1],
                },
            }
        await socket.send(json.dumps(payload, separators=(",", ":")))

    async def _heartbeat(
        self, socket: ClientConnection, interval: float
    ) -> None:
        """Send the Gateway heartbeat with the latest sequence number."""
        while True:
            await anyio.sleep(interval)
            self._heartbeat_ack = anyio.Event()
            await socket.send(
                json.dumps(
                    {"op": _HEARTBEAT, "d": self._sequence},
                    separators=(",", ":"),
                )
            )
            with anyio.fail_after(interval * 2):
                await self._heartbeat_ack.wait()

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        """Complete READY state or pass an event to the adapter queue."""
        event_type = frame.get("t")
        data = frame.get("d")
        if event_type == "READY":
            if not isinstance(data, dict) or not isinstance(
                data.get("session_id"), str
            ):
                raise QQProtocolError("QQ READY is missing session_id")
            self._session_id = data["session_id"]
            self._reconnect_attempt = 0
            resume_gateway_url = data.get("resume_gateway_url")
            if isinstance(resume_gateway_url, str) and (
                resume_gateway_url.startswith("wss://")
            ):
                self._resume_gateway_url = resume_gateway_url
            self.adapter.stream_connected(self.channel)
            return
        if event_type == "RESUMED":
            self._reconnect_attempt = 0
            self.adapter.stream_connected(self.channel)
            return
        if isinstance(event_type, str) and isinstance(data, dict):
            await self.adapter.accept_event(self.channel, frame)

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        """Decode and validate one JSON Gateway frame."""
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise QQProtocolError("QQ Gateway frame is not JSON") from error
        if not isinstance(frame, dict):
            raise QQProtocolError("QQ Gateway frame is not an object")
        return frame
