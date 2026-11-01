"""Normalize authenticated Stream callbacks without retaining bearer webhook URLs."""

from datetime import UTC, datetime
from typing import Any

from opsmesh.framework.adapters import BotConnectionConfiguration, BotInboundEvent, BotMessageEvent
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.bots.dingtalk.configuration import DingTalkConfiguration
from opsmesh.infrastructure.bots.dingtalk.message import decode_message


class DingTalkMessageEvent(BotMessageEvent):
    conversation_type: str

    def get_plaintext(self) -> str:
        return "".join(
            str(segment.data.get("text", ""))
            for segment in self.message
            if segment.type in {"text", "markdown"}
        )


def decode_event(
    payload: dict[str, Any],
    configuration: BotConnectionConfiguration,
    settings: DingTalkConfiguration,
) -> BotInboundEvent:
    robot_code = payload.get("robotCode")
    if robot_code is not None and robot_code != (settings.robot_code or configuration.self_id):
        raise OpsMeshError("UNAUTHORIZED", "DingTalk callback identifies another robot")
    user_id, message_id = payload.get("senderStaffId"), payload.get("msgId")
    conversation_type = payload.get("conversationType")
    if (
        not isinstance(user_id, str)
        or not user_id
        or not isinstance(message_id, str)
        or not message_id
    ):
        raise OpsMeshError("INVALID_REQUEST", "DingTalk callback lacks a staff or message identity")
    if conversation_type == "1":
        channel_id = f"private:{user_id}"
    elif (
        conversation_type == "2"
        and isinstance(payload.get("conversationId"), str)
        and payload["conversationId"]
    ):
        channel_id = f"group:{payload['conversationId']}"
    else:
        raise OpsMeshError("INVALID_REQUEST", "DingTalk callback has an unsupported conversation")
    timestamp = payload.get("createAt")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise OpsMeshError("INVALID_REQUEST", "DingTalk callback lacks a valid creation timestamp")
    try:
        occurred_at = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
    except (ValueError, OSError, OverflowError) as error:
        raise OpsMeshError("INVALID_REQUEST", "DingTalk callback timestamp is invalid") from error
    message = decode_message(payload)
    # Explicit allowlist: sessionWebhook, download credentials and arbitrary raw fields
    # must not reach durable event snapshots or plugin inspection.
    safe_payload = {
        "msgId": message_id,
        "senderStaffId": user_id,
        "conversationType": conversation_type,
        "conversationId": payload.get("conversationId"),
        "robotCode": robot_code,
        "createAt": timestamp,
        "message": message.model_dump(mode="json"),
    }
    event = DingTalkMessageEvent(
        id=f"dingtalk:{configuration.id}:{message_id}",
        source=f"dingtalk:{configuration.id}",
        occurred_at=occurred_at,
        payload=safe_payload,
        message=message,
        message_id=message_id,
        user_id=user_id,
        session_id=f"dingtalk:{configuration.id}:{channel_id}",
        channel_id=channel_id,
        conversation_type=conversation_type,
        to_me=conversation_type == "1" or payload.get("isInAtList") is True,
    )
    return BotInboundEvent(event=event, payload=safe_payload)
