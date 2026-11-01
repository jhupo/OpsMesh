"""QQ Gateway framing, heartbeat and ordered, bounded event acceptance."""

import asyncio
import json
import platform
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from opsmesh.framework.adapters import BotConnectionConfiguration, BotEventConsumer
from opsmesh.framework.drivers import WebSocket
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq.configuration import QQBotSettings
from opsmesh.infrastructure.bots.qq.event import decode_event


@dataclass
class QQGatewayState:
    """Resume only after the platform has accepted each dispatch in sequence."""

    session_id: str | None = None
    sequence: int | None = None
    bot_user_id: str | None = None

    def reset(self) -> None:
        self.session_id, self.sequence, self.bot_user_id = None, None, None


class QQGatewaySession:
    def __init__(
        self,
        socket: WebSocket,
        configuration: BotConnectionConfiguration,
        settings: QQBotSettings,
        state: QQGatewayState,
        consume: BotEventConsumer,
    ) -> None:
        self.socket, self.configuration, self.settings = socket, configuration, settings
        self.state, self.consume = state, consume
        self.callbacks: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=settings.callback_queue_size
        )
        self.ready = asyncio.Event()
        self.closed = False
        self._received_sequence = state.sequence
        self._awaiting_ack = False
        self._send_lock = asyncio.Lock()

    async def run(self, access_token: str) -> None:
        tasks: set[asyncio.Task[None]] = set()
        try:
            hello = await asyncio.wait_for(
                self._frame(), timeout=self.settings.handshake_timeout_seconds
            )
            data = hello.get("d")
            interval = data.get("heartbeat_interval") if isinstance(data, dict) else None
            if (
                hello.get("op") != 10
                or isinstance(interval, bool)
                or not isinstance(interval, (int, float))
                or not 0 < interval <= 300_000
            ):
                raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ Gateway did not send a valid Hello")
            if self.state.session_id is not None and self.state.sequence is not None:
                await self._send(
                    6,
                    {
                        "token": f"QQBot {access_token}",
                        "session_id": self.state.session_id,
                        "seq": self.state.sequence,
                    },
                )
            else:
                await self._send(
                    2,
                    {
                        "token": f"QQBot {access_token}",
                        "intents": self.settings.intents,
                        "shard": list(self.settings.shard),
                        "properties": {
                            "$os": platform.system().lower(),
                            "$browser": "opsmesh",
                            "$device": "opsmesh",
                        },
                    },
                )
            # The token never becomes a persisted Gateway/session property.
            tasks = {
                asyncio.create_task(self._receive(), name="qq-gateway-receiver"),
                asyncio.create_task(self._consume_callbacks(), name="qq-gateway-consumer"),
                asyncio.create_task(self._heartbeat(interval / 1000), name="qq-gateway-heartbeat"),
                asyncio.create_task(self._await_ready(), name="qq-gateway-handshake"),
            }
            finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                task.result()
        finally:
            self.closed = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(Exception):
                await asyncio.wait_for(self.socket.close(1001), timeout=5)

    async def _await_ready(self) -> None:
        await asyncio.wait_for(self.ready.wait(), timeout=self.settings.handshake_timeout_seconds)
        await asyncio.Future[None]()

    async def _send(self, opcode: int, data: Any) -> None:
        async with self._send_lock:
            await asyncio.wait_for(
                self.socket.send(json.dumps({"op": opcode, "d": data})),
                timeout=self.settings.handshake_timeout_seconds,
            )

    async def _frame(self) -> dict[str, Any]:
        raw = await self.socket.receive()
        if len(raw.encode() if isinstance(raw, str) else raw) > 1024 * 1024:
            raise OpsMeshError("INPUT_TOO_LARGE", "QQ Gateway frame exceeds 1 MiB")
        try:
            frame = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ Gateway sent invalid JSON") from error
        if not isinstance(frame, dict) or type(frame.get("op")) is not int:
            raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ Gateway frame is invalid")
        return frame

    async def _heartbeat(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            if self._awaiting_ack:
                raise OpsMeshError("BOT_UNAVAILABLE", "QQ Gateway heartbeat was not acknowledged")
            self._awaiting_ack = True
            await self._send(1, self._received_sequence)

    async def _receive(self) -> None:
        while True:
            frame = await self._frame()
            opcode = frame["op"]
            if opcode == 11:
                self._awaiting_ack = False
            elif opcode == 1:
                # A server-requested heartbeat also uses the latest received sequence.
                self._awaiting_ack = True
                await self._send(1, self._received_sequence)
            elif opcode == 7:
                return
            elif opcode == 9:
                self.state.reset()
                return
            elif opcode == 0:
                sequence = frame.get("s")
                if type(sequence) is not int or sequence < 0:
                    raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ dispatch sequence is invalid")
                self._received_sequence = sequence
                try:
                    self.callbacks.put_nowait(frame)
                except asyncio.QueueFull as error:
                    # Reconnect from the last accepted sequence; do not silently drop events.
                    raise OpsMeshError(
                        "BOT_UNAVAILABLE", "QQ Gateway event queue is full"
                    ) from error
            else:
                raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ Gateway opcode is unsupported")

    async def _consume_callbacks(self) -> None:
        while True:
            frame = await self.callbacks.get()
            try:
                kind, data = frame.get("t"), frame.get("d")
                if kind == "READY":
                    session_id = data.get("session_id") if isinstance(data, dict) else None
                    user = data.get("user") if isinstance(data, dict) else None
                    user_id = user.get("id") if isinstance(user, dict) else None
                    if not isinstance(session_id, str) or not 0 < len(session_id) <= 1024:
                        raise OpsMeshError(
                            "BOT_PROTOCOL_ERROR", "QQ READY lacks a session identity"
                        )
                    self.state.session_id = session_id
                    self.state.bot_user_id = (
                        user_id if isinstance(user_id, str) and 0 < len(user_id) <= 1024 else None
                    )
                    self.ready.set()
                elif kind == "RESUMED":
                    self.ready.set()
                else:
                    if self.state.session_id is None:
                        raise OpsMeshError("BOT_PROTOCOL_ERROR", "QQ dispatch arrived before READY")
                    incoming = decode_event(
                        frame,
                        account_id=self.configuration.id,
                        self_id=self.settings.event_self_id(
                            frame, self.state.bot_user_id or self.configuration.self_id
                        ),
                    )
                    await asyncio.wait_for(
                        self.consume(self.configuration, incoming),
                        timeout=self.settings.callback_timeout_seconds,
                    )
                self.state.sequence = frame["s"]
            finally:
                self.callbacks.task_done()
