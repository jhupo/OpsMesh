"""QQ gateway adapter backed directly by Tencent's official ``qq-botpy`` SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from opsmesh.gateway.adapters.errors import OperationRejected
from opsmesh.gateway.adapters.schemas import (
    Capabilities,
    EventSink,
    EventType,
    Message,
    Operation,
    Receipt,
    Result,
)

from ..base import Adapter
from ..schemas import SendMessageParameters
from .api import call as call_native
from .api import catalogue
from .config import QQConfig
from .events import normalize
from .messages import native_message


class _Client:
    """Own one official SDK client and forward its native callbacks to the adapter."""

    def __init__(self, adapter: QQAdapter) -> None:
        from botpy import Client

        self._adapter = adapter
        self._client = Client(intents=adapter.config.intents.to_botpy(), bot_log=False)
        for name in (
            "on_ready",
            "on_resumed",
            "on_message_create",
            "on_at_message_create",
            "on_direct_message_create",
            "on_group_at_message_create",
            "on_c2c_message_create",
            "on_message_delete",
            "on_direct_message_delete",
            "on_public_message_delete",
            "on_message_audit_pass",
            "on_message_audit_reject",
            "on_message_reaction_add",
            "on_message_reaction_remove",
            "on_guild_member_add",
            "on_guild_member_update",
            "on_guild_member_remove",
            "on_interaction_create",
        ):
            setattr(self._client, name, self._callback(name.removeprefix("on_")))

    def _callback(self, name: str) -> Callable[[Any], Awaitable[None]]:
        async def callback(event: Any = None) -> None:
            if name in {"ready", "resumed"}:
                self._adapter.set_state("connected")
                await self._adapter.publish(normalize(name, {"event_id": name, "id": name}))
            elif event is not None:
                await self._adapter.publish(normalize(name, event))

        return callback

    @property
    def api(self) -> Any:
        return self._client.api

    async def start(self, app_id: str, secret: str) -> Any:
        return await self._client.start(app_id, secret)

    async def close(self) -> None:
        await self._client.close()


class QQAdapter(Adapter):
    name = "qq"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = QQConfig.model_validate(config)
        self.client = _Client(self)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            implementation="opsmesh.qq.botpy",
            version="1.2.1",
            conversations=["c2c", "group", "guild", "dms"],
            events=sorted(
                {
                    EventType.MESSAGE_CREATED.value,
                    EventType.MESSAGE_DELETED.value,
                    EventType.CONNECTION_READY.value,
                    EventType.CONNECTION_RESUMED.value,
                    EventType.MESSAGE_AUDIT_PASSED.value,
                    EventType.MESSAGE_AUDIT_REJECTED.value,
                    EventType.REACTION_ADDED.value,
                    EventType.REACTION_REMOVED.value,
                    EventType.MEMBER_JOINED.value,
                    EventType.MEMBER_UPDATED.value,
                    EventType.MEMBER_LEFT.value,
                    EventType.INTERACTION_CREATED.value,
                    EventType.PLATFORM_EVENT.value,
                }
            ),
            message_parts=["text", "markdown", "reference"],
            operations={
                "message.send": Operation(
                    parameters=SendMessageParameters.model_json_schema(),
                    result=Receipt.model_json_schema(),
                ),
                **catalogue(),
            },
            configuration={
                "sdk": "qq-botpy==1.2.1",
                "transport": "official websocket client",
                "intents": self.config.intents.model_dump(),
                "webhook": False,
            },
        )

    async def run(self, sink: EventSink) -> None:
        self.sink = sink
        self.set_state("connecting")
        try:
            await self.client.start(self.config.app_id, self.config.secret.get_secret_value())
        except asyncio.CancelledError:
            raise
        except Exception:
            self.set_state("failed")
            raise
        finally:
            await self.client.close()
            self.sink = None
            if self.state != "failed":
                self.set_state("stopped")

    async def send(self, message: Message) -> Receipt:
        self.require_ready()
        target = message.target or {}
        kind, destination = target.get("kind"), target.get("id")
        if kind not in {"c2c", "group", "guild", "dms"} or not isinstance(destination, str):
            raise OperationRejected("invalid_qq_target")
        payload = native_message(message.text, message.content)
        reply_id = target.get("message_id") or message.reply_to
        if reply_id:
            payload["msg_id"] = reply_id
        if kind == "c2c":
            result = await self.client.api.post_c2c_message(
                openid=destination, msg_seq=message.sequence, **payload
            )
        elif kind == "group":
            result = await self.client.api.post_group_message(
                group_openid=destination, msg_seq=message.sequence, **payload
            )
        elif kind == "guild":
            result = await self.client.api.post_message(channel_id=destination, **payload)
        else:
            result = await self.client.api.post_dms(guild_id=destination, **payload)
        value = to_jsonable_python(result)
        if not isinstance(value, dict) or not value.get("id"):
            raise ValueError("QQ send returned no platform message ID")
        return Receipt(value=value)

    async def call(self, operation: str, parameters: dict[str, Any]) -> Result:
        if operation not in self.capabilities.operations:
            raise OperationRejected("unknown_qq_operation")
        self.require_ready()
        if operation == "message.send":
            try:
                message = SendMessageParameters.model_validate(parameters).message()
            except ValidationError as exc:
                raise OperationRejected("invalid_message_parameters") from exc
            value = (await self.send(message)).model_dump(mode="json")
        else:
            value = await call_native(self.client.api, operation, parameters)
        return Result(operation=operation, value=value)
