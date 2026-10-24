import time
from typing import Any
from urllib.parse import urlsplit

from opsmesh_sdk.channels import OperationRejected


def session_url(target: dict[str, Any]) -> str:
    url = target.get("webhook")
    if not isinstance(url, str):
        raise OperationRejected("missing_dingtalk_session_webhook")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "oapi.dingtalk.com"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.path != "/robot/sendBySession"
            or parsed.fragment
        ):
            raise ValueError("Invalid session webhook")
        if float(target.get("expires_at", 0)) / 1000 <= time.time():
            raise OperationRejected("dingtalk_session_webhook_expired")
    except (ValueError, TypeError) as exc:
        raise OperationRejected("invalid_dingtalk_session_webhook") from exc
    return url


def webhook_payload(text: str, content: list[dict[str, Any]]) -> dict[str, Any]:
    if not content:
        return {"msgtype": "text", "text": {"content": text}}
    if len(content) != 1:
        raise OperationRejected("dingtalk_webhook_requires_one_message")
    segment = content[0]
    kind, data = segment.get("type"), segment.get("data")
    if kind not in {
        "text",
        "markdown",
        "link",
        "actionCard",
        "feedCard",
        "image",
    } or not isinstance(data, dict):
        raise OperationRejected("invalid_dingtalk_message")
    return {"msgtype": kind, kind: data}
