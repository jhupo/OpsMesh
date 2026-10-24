import asyncio
import json
import logging
from typing import Any

import dingtalk_stream
import httpx
from opsmesh_sdk.channels import ChannelAdapter, EventSink, OperationRejected, OutgoingMessage
from pydantic import ValidationError

from ..schemas import SendMessageParameters
from .api import DingTalkAPI
from .config import DingTalkConfig
from .handlers import register
from .messages import session_url, webhook_payload


class DingTalkChannelAdapter(ChannelAdapter):
    def __init__(self, config: dict[str, Any]):
        super().__init__()
        self.config = DingTalkConfig.model_validate(config)
        credential = dingtalk_stream.Credential(
            self.config.client_id, self.config.client_secret.get_secret_value()
        )
        self.transport_logger = logging.getLogger("opsmesh.dingtalk.transport")
        self.transport_logger.disabled = True
        self.client = dingtalk_stream.DingTalkStreamClient(credential, logger=self.transport_logger)
        self.http = httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False)
        self.api = DingTalkAPI(self.client, self.http)

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "sdk": "dingtalk-stream",
            "version": "0.24.4b1",
            "conversations": ["private", "group"],
            "events": ["robot", "delegate", "subscription", "card", "graph"],
            "enabled_callbacks": {
                "subscription": self.config.subscribe_events,
                "card": self.config.card_callbacks,
                "graph": self.config.graph_callbacks,
            },
            "operations": {
                "message.send": {"parameters": ["target", "text", "content"]},
                "api.request": {
                    "parameters": ["method", "path", "api", "query", "body"],
                    "hosts": ["api.dingtalk.com", "oapi.dingtalk.com"],
                },
                "media.upload": {"parameters": ["type", "filename", "content_base64"]},
            },
            "cards": {
                "create": "POST /v1.0/card/instances",
                "deliver": "POST /v1.0/card/instances/deliver",
                "update": "PUT /v1.0/card/instances",
                "stream": "PUT /v1.0/card/streaming",
            },
        }

    async def connect(self, sink: EventSink) -> None:
        register(self.client, self.config, sink, self.transport_logger)
        connection = asyncio.create_task(self.client.start())
        try:
            while not connection.done():
                if self.client.websocket is not None:
                    self.ready.set()
                else:
                    self.ready.clear()
                await asyncio.sleep(0.1)
            await connection
        finally:
            self.ready.clear()
            await self.client.stop()
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)

    async def send(self, message: OutgoingMessage) -> Any:
        target = message.target or {}
        if target.get("kind") == "session":
            response = await self.http.post(
                session_url(target), json=webhook_payload(message.text, message.content)
            )
            return self.api.response(response, webhook=True)
        robot_code = self.config.robot_code or self.config.client_id
        if message.content:
            if len(message.content) != 1 or message.content[0].get("type") != "markdown":
                raise OperationRejected("proactive_message_requires_text_or_markdown")
            msg_key, msg_param = "sampleMarkdown", message.content[0]["data"]
        else:
            msg_key, msg_param = "sampleText", {"content": message.text}
        body = {
            "robotCode": robot_code,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
        }
        if (
            target.get("kind") == "private"
            and isinstance(target.get("user_ids"), list)
            and target["user_ids"]
        ):
            path = "/v1.0/robot/oToMessages/batchSend"
            body["userIds"] = target["user_ids"]
        elif target.get("kind") == "group" and target.get("conversation_id"):
            path = "/v1.0/robot/groupMessages/send"
            body["openConversationId"] = target["conversation_id"]
        else:
            raise OperationRejected("invalid_dingtalk_target")
        return await self.api.request({"method": "POST", "path": path, "body": body})

    async def execute(self, operation: str, parameters: dict[str, Any]) -> Any:
        if operation == "api.request":
            return await self.api.request(parameters)
        if operation == "media.upload":
            return await self.api.upload(parameters)
        if operation == "message.send":
            try:
                message = SendMessageParameters.model_validate(parameters).message()
            except ValidationError as exc:
                raise OperationRejected("invalid_message_parameters") from exc
            return await self.send(message)
        raise OperationRejected("unknown_dingtalk_operation")

    async def close(self) -> None:
        self.ready.clear()
        await self.client.stop()
        await self.http.aclose()
