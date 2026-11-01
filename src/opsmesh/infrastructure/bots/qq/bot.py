"""Official QQ message APIs with explicit target and passive-reply semantics."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from opsmesh.framework.adapters import (
    BotConnectionConfiguration,
    BotRef,
    OpsMeshBot,
    OpsMeshEvent,
    OpsMeshMessage,
    ReplyTarget,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.qq.client import QQAPIClient
from opsmesh.infrastructure.bots.qq.event import QQMessageEvent
from opsmesh.infrastructure.bots.qq.media import media_upload
from opsmesh.infrastructure.bots.qq.message import encode_message

if TYPE_CHECKING:
    from opsmesh.infrastructure.bots.qq.adapter import QQAdapter


class QQBot(OpsMeshBot):
    def __init__(
        self, adapter: QQAdapter, configuration: BotConnectionConfiguration, client: QQAPIClient
    ) -> None:
        super().__init__(adapter, configuration.self_id)
        self.configuration, self.client = configuration, client

    @property
    def ref(self) -> BotRef:
        return BotRef(adapter_id=self.type, bot_id=self.configuration.id)

    async def send(self, target: OpsMeshEvent | ReplyTarget, message: OpsMeshMessage) -> str:
        if isinstance(target, QQMessageEvent):
            if target.account_id != self.configuration.id:
                raise OpsMeshError("INVALID_REQUEST", "QQ message belongs to another bot")
            target = ReplyTarget(
                bot=self.ref, channel_id=target.channel_id, message_id=target.message_id
            )
        if not isinstance(target, ReplyTarget) or target.bot != self.ref:
            raise OpsMeshError("INVALID_REQUEST", "QQ requires a reply target for this bot")
        if target.thread_id:
            raise OpsMeshError("UNSUPPORTED_FEATURE", "QQ message replies do not support threads")
        kind, separator, identifier = target.channel_id.partition(":")
        if not separator:
            raise OpsMeshError("INVALID_REQUEST", "QQ target requires a conversation kind")
        upload = media_upload(message)
        if upload is None:
            data = encode_message(message, typed=kind in {"c2c", "group"})
        else:
            if kind not in {"c2c", "group"}:
                raise OpsMeshError("UNSUPPORTED_FEATURE", "QQ media supports groups and C2C")
            uploaded = await self.call_api(f"upload_{kind}_media", target_id=identifier, **upload)
            file_info = uploaded.get("file_info")
            if not isinstance(file_info, str) or not file_info:
                raise OpsMeshError("BOT_UNAVAILABLE", "QQ did not confirm the media upload")
            data = {
                "msg_type": 7,
                "content": message.extract_plain_text() or " ",
                "media": {"file_info": file_info},
            }
        if target.message_id:
            data["msg_id"] = target.message_id
            if kind in {"c2c", "group"}:
                data["msg_seq"] = target.delivery_sequence
        result = await self.call_api(f"send_{kind}_message", target_id=identifier, **data)
        message_id = result.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise OpsMeshError("DELIVERY_UNKNOWN", "QQ did not confirm a message identity")
        return message_id

    async def remove_message(self, target: ReplyTarget) -> None:
        if target.bot != self.ref or not target.message_id:
            raise OpsMeshError(
                "INVALID_REQUEST", "QQ removal requires a message target for this bot"
            )
        kind, separator, identifier = target.channel_id.partition(":")
        if not separator or kind != "group" or target.thread_id:
            raise OpsMeshError("UNSUPPORTED_FEATURE", "QQ removal supports group messages only")
        await self.call_api(
            "delete_group_message", target_id=identifier, message_id=target.message_id
        )

    async def call_api(self, api: str, **data: Any) -> dict[str, Any]:
        paths = {
            "send_c2c_message": "/v2/users/{}/messages",
            "send_group_message": "/v2/groups/{}/messages",
            "send_channel_message": "/channels/{}/messages",
            "send_dms_message": "/dms/{}/messages",
            "upload_group_media": "/v2/groups/{}/files",
            "upload_c2c_media": "/v2/users/{}/files",
        }
        reads = {
            "get_group_state": "/v2/groups/{}/bot_state",
            "get_group_info": "/v2/groups/{}/info",
        }
        if api == "get_self":
            if data:
                raise OpsMeshError("INVALID_REQUEST", "QQ get_self does not accept parameters")
            return await self.client.get("/users/@me")
        if api not in paths and api not in reads and api != "delete_group_message":
            raise OpsMeshError("UNSUPPORTED_FEATURE", "QQ API operation is not supported")
        identifier = data.pop("target_id", None)
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", identifier):
            raise OpsMeshError("INVALID_REQUEST", "QQ target identifier is invalid")
        encoded = quote(identifier, safe="")
        if api in reads:
            if data:
                raise OpsMeshError("INVALID_REQUEST", "QQ group query has unexpected parameters")
            return await self.client.get(reads[api].format(encoded))
        if api == "delete_group_message":
            message_id = data.pop("message_id", None)
            if (
                data
                or not isinstance(message_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_.=+-]{1,2048}", message_id)
                or message_id in {".", ".."}
            ):
                raise OpsMeshError("INVALID_REQUEST", "QQ recall requires a valid message ID")
            return await self.client.delete(
                f"/v2/groups/{encoded}/messages/{quote(message_id, safe='')}"
            )
        return await self.client.post(paths[api].format(encoded), data)
