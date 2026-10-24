import asyncio
import socket
from typing import Any, cast

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.exception import ActionFailed, AuditException
from opsmesh_sdk.channels import ChannelAdapter, EventSink, OperationRejected, OutgoingMessage
from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from uvicorn import Config as ServerConfig

from ..schemas import SendMessageParameters
from .api import call, catalogue
from .config import QQConfig
from .driver import build_driver
from .messages import native_message
from .webhook import WebhookServer


class QQChannelAdapter(ChannelAdapter):
    def __init__(self, config: dict[str, Any]):
        super().__init__()
        self.config = QQConfig.model_validate(config)
        self.native: Any = None
        self.stopped = asyncio.Event()
        self.server: WebhookServer | None = None

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "sdk": "nonebot-adapter-qq",
            "version": "1.7.3",
            "conversations": ["c2c", "group", "guild", "dms"],
            "transports": ["websocket", "webhook"],
            "configured_transport": self.config.transport,
            "configured_intents": self.config.intents.model_dump(),
            "events": "all native event types; messages invoke bound agents, other events are persisted",
            "operations": {
                "message.send": {
                    "parameters": ["target", "text", "content", "reply_to", "sequence"]
                },
                **catalogue(),
            },
        }

    async def connect(self, sink: EventSink) -> None:
        driver, self.native = build_driver(self.config, sink, self.ready)
        try:
            if self.config.transport == "webhook":
                self.server = WebhookServer(
                    ServerConfig(
                        driver.server_app,
                        host=self.config.webhook_host,
                        port=self.config.webhook_port,
                        log_config=None,
                        access_log=False,
                        timeout_graceful_shutdown=5,
                    )
                )
                family = socket.AF_INET6 if ":" in self.config.webhook_host else socket.AF_INET
                # Bind before starting Uvicorn: a conflicting port raises OSError in this
                # channel instead of Uvicorn's process-wide SystemExit path.
                with socket.create_server(
                    (self.config.webhook_host, self.config.webhook_port), family=family
                ) as listener:
                    server_task = asyncio.create_task(self.server.serve(sockets=[listener]))
                    try:
                        await asyncio.shield(server_task)
                    finally:
                        self.server.should_exit = True
                        try:
                            async with asyncio.timeout(10):
                                await asyncio.shield(server_task)
                        except TimeoutError:
                            server_task.cancel()
                            await asyncio.gather(server_task, return_exceptions=True)
            else:
                async with driver.server_app.router.lifespan_context(driver.server_app):
                    while not self.stopped.is_set():
                        # A failed discovery/auth attempt can leave no native transport task.
                        if not self.native.tasks:
                            raise RuntimeError("QQ native transport stopped")
                        try:
                            await asyncio.wait_for(self.stopped.wait(), 1)
                        except TimeoutError:
                            pass
        finally:
            self.ready.clear()

    def bot(self) -> Bot:
        if not self.ready.is_set() or self.native is None:
            raise OperationRejected("qq_not_connected")
        return cast(Bot, self.native.bots[self.config.app_id])

    async def send(self, message: OutgoingMessage) -> Any:
        target = message.target or {}
        kind, destination = target.get("kind"), target.get("id")
        if (
            kind not in {"c2c", "group", "guild", "dms"}
            or not isinstance(destination, str)
            or not destination
        ):
            raise OperationRejected("invalid_qq_target")
        content = native_message(message.text, message.content)
        bot = self.bot()
        reply_id = target.get("message_id") or message.reply_to
        result: Any
        try:
            if kind == "c2c":
                result = await bot.send_to_c2c(
                    destination, content, msg_id=reply_id, msg_seq=message.sequence
                )
            elif kind == "group":
                result = await bot.send_to_group(
                    destination, content, msg_id=reply_id, msg_seq=message.sequence
                )
            elif kind == "guild":
                result = await bot.send_to_channel(destination, content, msg_id=reply_id)
            else:
                result = await bot.send_to_dms(destination, content, msg_id=reply_id)
        except ActionFailed as exc:
            if 400 <= exc.status_code < 500:
                raise OperationRejected(f"qq_rejected_{exc.status_code}_{exc.code or 0}") from exc
            raise
        except AuditException as exc:
            return {"status": "pending_audit", "audit_id": exc.audit_id}
        return to_jsonable_python(result)

    async def execute(self, operation: str, parameters: dict[str, Any]) -> Any:
        if operation == "message.send":
            try:
                message = SendMessageParameters.model_validate(parameters).message()
            except ValidationError as exc:
                raise OperationRejected("invalid_message_parameters") from exc
            return await self.send(message)
        return await call(self.bot(), operation, parameters)

    async def close(self) -> None:
        self.stopped.set()
        self.ready.clear()
        if self.server:
            self.server.should_exit = True
