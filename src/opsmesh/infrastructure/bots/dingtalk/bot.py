"""DingTalk application robot sends through OpenAPI for persistent conversation targets."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from opsmesh.framework.adapters import BotRef, OpsMeshBot, OpsMeshEvent, OpsMeshMessage, ReplyTarget
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.dingtalk.client import DingTalkAPIClient
from opsmesh.infrastructure.bots.dingtalk.event import DingTalkMessageEvent
from opsmesh.infrastructure.bots.dingtalk.message import encode_message

if TYPE_CHECKING:
    from opsmesh.infrastructure.bots.dingtalk.adapter import DingTalkAdapter


class DingTalkBot(OpsMeshBot):
    def __init__(self, adapter: DingTalkAdapter, api_client: DingTalkAPIClient) -> None:
        super().__init__(adapter, api_client.configuration.self_id)
        self.api_client = api_client
        self.configuration = api_client.configuration

    @property
    def ref(self) -> BotRef:
        return BotRef(adapter_id="dingtalk", bot_id=self.configuration.id)

    async def send(self, target: OpsMeshEvent | ReplyTarget, message: OpsMeshMessage) -> str:
        if isinstance(target, DingTalkMessageEvent):
            if target.source != f"dingtalk:{self.configuration.id}":
                raise OpsMeshError("INVALID_REQUEST", "DingTalk event belongs to another bot")
            channel_id = target.channel_id
        elif isinstance(target, ReplyTarget):
            if target.bot != self.ref:
                raise OpsMeshError(
                    "INVALID_REQUEST", "DingTalk reply target belongs to another bot"
                )
            if target.thread_id:
                raise OpsMeshError(
                    "UNSUPPORTED_FEATURE", "DingTalk adapter does not support thread replies"
                )
            channel_id = target.channel_id
        else:
            raise OpsMeshError(
                "INVALID_REQUEST", "DingTalk requires an explicit conversation target"
            )
        kind, separator, identifier = channel_id.partition(":")
        if (
            not separator
            or kind not in {"private", "group"}
            or not identifier.strip()
            or any(ord(c) < 32 for c in identifier)
        ):
            raise OpsMeshError(
                "INVALID_REQUEST",
                "DingTalk target must be private:<staffId> or group:<conversationId>",
            )
        message_key, parameters = encode_message(message)
        payload: dict[str, Any] = {
            "robotCode": self.api_client.robot_code,
            "msgKey": message_key,
            "msgParam": json.dumps(parameters, ensure_ascii=False, separators=(",", ":")),
        }
        api_name = "group_messages_send" if kind == "group" else "private_messages_send"
        payload["openConversationId" if kind == "group" else "userIds"] = (
            identifier if kind == "group" else [identifier]
        )
        result = await self.call_api(api_name, **payload)
        if result.get("invalidUserIds") or result.get("flowControlledUserIds"):
            raise OpsMeshError("BOT_REQUEST_REJECTED", "DingTalk did not accept the recipient")
        message_id = result.get("processQueryKey")
        if not isinstance(message_id, str) or not message_id:
            raise OpsMeshError("DELIVERY_UNKNOWN", "DingTalk did not return a delivery identity")
        return message_id

    async def call_api(self, api: str, **data: Any) -> Any:
        routes = {
            "group_messages_send": "/v1.0/robot/groupMessages/send",
            "private_messages_send": "/v1.0/robot/oToMessages/batchSend",
        }
        if api not in routes:
            raise OpsMeshError(
                "UNSUPPORTED_FEATURE", "DingTalk API is not supported by this adapter"
            )
        if data.get("robotCode") != self.api_client.robot_code:
            raise OpsMeshError("INVALID_REQUEST", "DingTalk API cannot target another robot")
        return await self.api_client.send(routes[api], data)
