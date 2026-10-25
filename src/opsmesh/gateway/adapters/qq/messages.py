"""Translate host message parts into official botpy request payloads."""

import base64
import binascii
from typing import Any

from opsmesh.gateway.adapters.errors import OperationRejected


def binary_values(value: Any) -> Any:
    """Decode the host's explicit binary envelope; filesystem paths are never accepted."""
    if isinstance(value, dict):
        if set(value) == {"$base64"}:
            try:
                return base64.b64decode(value["$base64"], validate=True)
            except (ValueError, TypeError, binascii.Error) as exc:
                raise OperationRejected("invalid_binary_payload") from exc
        return {key: binary_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [binary_values(item) for item in value]
    return value


def native_message(text: str, content: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a botpy ``post_*_message`` payload from portable gateway parts."""
    parts = binary_values(content)
    if not isinstance(parts, list):
        raise OperationRejected("invalid_qq_message")
    payload: dict[str, Any] = {}
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("type"), str):
            raise OperationRejected("invalid_qq_message")
        kind = part["type"]
        data = part.get("data", {})
        if kind == "text":
            text += str(data.get("text", ""))
        elif kind == "markdown":
            payload["msg_type"] = 2
            payload["markdown"] = data
        elif kind == "reference":
            payload["message_reference"] = data
        elif kind in {"mention_user", "mention_channel", "mention_everyone", "emoji"}:
            raise OperationRejected("qq_message_part_not_supported")
        else:
            raise OperationRejected("qq_message_part_not_supported")
    if text:
        payload["content"] = text
        payload.setdefault("msg_type", 0)
    if not payload:
        raise OperationRejected("empty_qq_message")
    return payload
