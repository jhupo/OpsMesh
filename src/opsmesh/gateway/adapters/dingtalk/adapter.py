import asyncio
import json
import logging
from typing import Any

import dingtalk_stream
import httpx
from pydantic import ValidationError

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
from .api import DingTalkAPI, MediaUpload
from .config import DingTalkConfig
from .handlers import register
from .messages import session_url, webhook_payload
from .schemas import CARD_OPERATIONS, CardResponse, CreateCard, DeliverCard


class DingTalkAdapter(Adapter):
    name = "dingtalk"

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
    def capabilities(self) -> Capabilities:
        callbacks = ["robot", "delegate"]
        events = [EventType.MESSAGE_CREATED.value]
        if self.config.subscribe_events:
            callbacks.append("subscription")
        if self.config.card_callbacks:
            callbacks.append("card")
            events.append(EventType.INTERACTION_CREATED.value)
        if self.config.graph_callbacks:
            callbacks.append("graph")
        if self.config.subscribe_events or self.config.graph_callbacks:
            events.append(EventType.PLATFORM_EVENT.value)
        return Capabilities(
            implementation="dingtalk-stream",
            version="0.24.4b1",
            conversations=["private", "group"],
            events=events,
            message_parts=[
                "text",
                "image",
                "file",
                "audio",
                "video",
                "markdown",
                "link",
                "mention",
                "platform",
            ],
            operations={
                "message.send": Operation(
                    parameters=SendMessageParameters.model_json_schema(),
                    result=Receipt.model_json_schema(),
                ),
                "media.upload": Operation(
                    parameters=MediaUpload.model_json_schema(),
                    result={
                        "type": "object",
                        "required": ["media_id"],
                        "properties": {"media_id": {"type": "string"}},
                    },
                ),
                **{
                    name: Operation(
                        parameters=model.model_json_schema(),
                        result=CardResponse.model_json_schema(),
                    )
                    for name, (_, _, model) in CARD_OPERATIONS.items()
                },
            },
            configuration={
                "callbacks": callbacks,
                "inbound_message_parts": [
                    "text",
                    "image",
                    "file",
                    "audio",
                    "video",
                    "mention",
                    "link",
                    "platform",
                ],
                "native_inbound_message_types": [
                    "text",
                    "picture",
                    "richText",
                    "file",
                    "audio",
                    "video",
                ],
                "proactive_message_parts": ["text", "markdown"],
                "session_message_parts": [
                    "text",
                    "markdown",
                    "link",
                    "actionCard",
                    "feedCard",
                    "image",
                ],
            },
        )

    async def run(self, sink: EventSink) -> None:
        self.sink = sink
        self.set_state("connecting")
        connection: asyncio.Task[None] | None = None
        try:
            register(self.client, self.config, self.publish, self.transport_logger)
            connection = asyncio.create_task(self.client.start())
            while not connection.done():
                self.set_state("connected" if self.client.websocket is not None else "reconnecting")
                await asyncio.sleep(0.1)
            await connection
            raise RuntimeError("DingTalk transport stopped")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.set_state("failed")
            raise
        finally:
            self.ready.clear()
            try:
                await self.client.stop()
            finally:
                if connection is not None:
                    connection.cancel()
                    await asyncio.gather(connection, return_exceptions=True)
                try:
                    await self.http.aclose()
                finally:
                    self.sink = None
                    if self.state != "failed":
                        self.set_state("stopped")

    async def send(self, message: Message) -> Receipt:
        self.require_ready()
        target = message.target or {}
        if target.get("kind") == "session":
            response = await self.http.post(
                session_url(target), json=webhook_payload(message.text, message.content)
            )
            return Receipt(value=self.api.response(response, webhook=True))
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
        data = await self.api.request("POST", path, body)
        if not isinstance(data.get("processQueryKey"), str) or not data["processQueryKey"]:
            raise ValueError("DingTalk send returned no process query key")
        return Receipt(value=data)

    async def call(self, operation: str, parameters: dict[str, Any]) -> Result:
        if operation not in self.capabilities.operations:
            raise OperationRejected("unknown_dingtalk_operation")
        self.require_ready()
        if operation == "media.upload":
            value = await self.api.upload(parameters)
        elif operation == "message.send":
            try:
                message = SendMessageParameters.model_validate(parameters).message()
            except ValidationError as exc:
                raise OperationRejected("invalid_message_parameters") from exc
            value = (await self.send(message)).model_dump(mode="json")
        else:
            method, path, model = CARD_OPERATIONS[operation]
            try:
                request = model.model_validate(parameters)
                if (
                    isinstance(request, CreateCard)
                    and request.callbackType == "HTTP"
                    and not request.callbackRouteKey
                ):
                    raise ValueError("HTTP cards require a callback route key")
                if isinstance(request, DeliverCard):
                    group = request.imGroupOpenDeliverModel is not None
                    robot = request.imRobotOpenDeliverModel is not None
                    if group == robot or not request.openSpaceId.startswith(
                        "dtv1.card//IM_GROUP." if group else "dtv1.card//IM_ROBOT."
                    ):
                        raise ValueError("Card delivery requires one matching destination")
            except (ValidationError, ValueError) as exc:
                raise OperationRejected("invalid_dingtalk_parameters") from exc
            value = await self.api.request(
                method, path, request.model_dump(mode="json", exclude_none=True)
            )
            CardResponse.model_validate(value)
        return Result(operation=operation, value=value)
