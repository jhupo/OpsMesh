import base64
import binascii
from typing import Any

from nonebot.adapters.qq.message import Message
from nonebot.compat import type_validate_python
from opsmesh_sdk.channels import OperationRejected


def binary_values(value: Any) -> Any:
    """JSON binary envelope; never accept a gateway worker's filesystem path."""
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


def native_message(text: str, content: list[dict[str, Any]]) -> Message:
    try:
        if content:
            return type_validate_python(Message, binary_values(content))
        return Message(text)
    except (ValueError, TypeError, KeyError) as exc:
        raise OperationRejected("invalid_qq_message") from exc
