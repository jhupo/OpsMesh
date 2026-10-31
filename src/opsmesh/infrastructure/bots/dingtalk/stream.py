"""DingTalk Stream transport with bounded callbacks and independent control frames."""

import asyncio
import json
from contextlib import suppress
from typing import Any

from opsmesh.framework.adapters import BotConnectionConfiguration, BotEventConsumer
from opsmesh.framework.drivers import WebSocket
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.dingtalk.configuration import DingTalkConfiguration
from opsmesh.infrastructure.bots.dingtalk.event import decode_event


class DingTalkStreamSession:
    callback_topic = "/v1.0/im/bot/messages/get"

    def __init__(
        self,
        socket: WebSocket,
        configuration: BotConnectionConfiguration,
        settings: DingTalkConfiguration,
        consume: BotEventConsumer,
    ) -> None:
        self.socket, self.configuration, self.settings, self.consume = (
            socket,
            configuration,
            settings,
            consume,
        )
        self.callbacks: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._send_lock = asyncio.Lock()
        self.closed = False

    async def run(self) -> None:
        worker = asyncio.create_task(self._consume_callbacks())
        receiver = asyncio.create_task(self._receive())
        try:
            finished, _ = await asyncio.wait(
                {worker, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in finished:
                task.result()
        finally:
            self.closed = True
            worker.cancel()
            receiver.cancel()
            await asyncio.gather(worker, receiver, return_exceptions=True)
            await self.socket.close()

    async def _ack(
        self, message_id: str, code: int = 200, *, data: dict[str, Any] | None = None
    ) -> None:
        response = {
            "code": code,
            "message": "OK" if code == 200 else "Callback rejected",
            "headers": {"messageId": message_id, "contentType": "application/json"},
            "data": json.dumps(data if data is not None else {"response": None}),
        }
        async with self._send_lock:
            await self.socket.send(json.dumps(response))

    @staticmethod
    def _data(frame: dict[str, Any]) -> dict[str, Any]:
        value = frame.get("data")
        try:
            payload = json.loads(value) if isinstance(value, str) else None
        except ValueError as error:
            raise OpsMeshError("BOT_PROTOCOL_ERROR", "Invalid DingTalk frame data") from error
        if not isinstance(payload, dict):
            raise OpsMeshError("BOT_PROTOCOL_ERROR", "DingTalk frame data must encode an object")
        return payload

    async def _receive(self) -> None:
        while True:
            raw = await self.socket.receive()
            if len(raw.encode() if isinstance(raw, str) else raw) > 1024 * 1024:
                raise OpsMeshError("INPUT_TOO_LARGE", "DingTalk frame exceeds 1 MiB")
            try:
                frame = json.loads(raw)
            except (ValueError, UnicodeError) as error:
                raise OpsMeshError("BOT_PROTOCOL_ERROR", "Invalid DingTalk frame") from error
            if not isinstance(frame, dict) or not isinstance(frame.get("headers"), dict):
                raise OpsMeshError("BOT_PROTOCOL_ERROR", "DingTalk frame lacks headers")
            headers = frame["headers"]
            topic, message_id = headers.get("topic"), headers.get("messageId")
            if frame.get("type") == "SYSTEM" and topic == "disconnect":
                # No ACK is required; allow already queued callbacks a short graceful drain.
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.callbacks.join(), timeout=10)
                return
            if not isinstance(message_id, str) or not message_id:
                raise OpsMeshError("BOT_PROTOCOL_ERROR", "DingTalk frame lacks an ACK identity")
            if frame.get("type") == "SYSTEM" and topic == "ping":
                data = self._data(frame)
                await self._ack(message_id, data={"opaque": data.get("opaque")})
            elif frame.get("type") == "CALLBACK" and topic == self.callback_topic:
                try:
                    self.callbacks.put_nowait(frame)
                except asyncio.QueueFull:
                    # Backpressure must not block ping/disconnect processing.
                    await self._ack(message_id, 500)
            else:
                await self._ack(message_id, 404)

    async def _consume_callbacks(self) -> None:
        while True:
            frame = await self.callbacks.get()
            try:
                try:
                    incoming = decode_event(self._data(frame), self.configuration, self.settings)
                    await self.consume(self.configuration, incoming)
                except OpsMeshError as error:
                    code = (
                        400
                        if error.code
                        in {"INVALID_REQUEST", "UNAUTHORIZED", "FORBIDDEN", "UNSUPPORTED_FEATURE"}
                        else 500
                    )
                except Exception:
                    code = 500
                else:
                    code = 200
                await self._ack(frame["headers"]["messageId"], code)
            finally:
                self.callbacks.task_done()
