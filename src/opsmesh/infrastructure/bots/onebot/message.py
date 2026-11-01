"""OneBot array/CQ message conversion, retaining namespaced protocol segments.

Protocol: https://github.com/botuniverse/onebot-11/tree/master/message
This implementation is original; no upstream implementation code is copied.
"""

from __future__ import annotations

import re
from typing import Any

from opsmesh.framework.adapters import OpsMeshMessage, OpsMeshMessageSegment
from opsmesh.framework.errors import OpsMeshError


def _unescape(text: str) -> str:
    return (
        text.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")
    )


def decode_message(value: Any) -> OpsMeshMessage:
    if isinstance(value, str):
        parts: list[dict[str, Any]] = []
        position = 0
        for match in re.finditer(r"\[CQ:([a-zA-Z0-9_]+)((?:,[^\]]*)?)\]", value):
            if match.start() > position:
                parts.append(
                    {"type": "text", "data": {"text": _unescape(value[position : match.start()])}}
                )
            parameters = {}
            for pair in match.group(2).lstrip(",").split(","):
                if pair and "=" in pair:
                    name, raw = pair.split("=", 1)
                    parameters[name] = _unescape(raw)
            parts.append({"type": match.group(1), "data": parameters})
            position = match.end()
        if position < len(value):
            parts.append({"type": "text", "data": {"text": _unescape(value[position:])}})
        value = parts
    if not isinstance(value, list):
        raise OpsMeshError("INVALID_REQUEST", "OneBot message must be a string or segment array")
    result = []
    for segment in value:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            raise OpsMeshError("INVALID_REQUEST", "Malformed OneBot message segment")
        kind = segment.get("type")
        if not isinstance(kind, str):
            raise OpsMeshError("INVALID_REQUEST", "OneBot segment type must be a string")
        if kind == "text":
            result.append(OpsMeshMessageSegment.text(str(segment["data"].get("text", ""))))
        else:
            result.append(OpsMeshMessageSegment(type=f"onebot:{kind}", data=segment["data"]))
    return OpsMeshMessage(result)


def encode_message(message: OpsMeshMessage) -> list[dict[str, Any]]:
    result = []
    for segment in message:
        if segment.type == "text":
            result.append({"type": "text", "data": {"text": str(segment)}})
        elif segment.type.startswith("onebot:"):
            result.append({"type": segment.type.partition(":")[2], "data": dict(segment.data)})
        else:
            raise OpsMeshError(
                "UNSUPPORTED_FEATURE",
                f"OneBot cannot send segment {segment.type!r}; "
                "resolve resources to explicit OneBot segments first",
            )
    return result
