"""Explicit DingTalk text and markdown conversion; no lossy attachment coercion."""

from typing import Any

from opsmesh.framework.adapters import OpsMeshMessage, OpsMeshMessageSegment
from opsmesh.framework.errors import OpsMeshError


def decode_message(payload: dict[str, Any]) -> OpsMeshMessage:
    kind = payload.get("msgtype")
    if kind == "text":
        content = (
            payload.get("text", {}).get("content")
            if isinstance(payload.get("text"), dict)
            else None
        )
        if isinstance(content, str):
            return OpsMeshMessage(content)
    elif kind == "markdown":
        markdown = payload.get("markdown")
        if isinstance(markdown, dict) and isinstance(markdown.get("text"), str):
            return OpsMeshMessage(
                OpsMeshMessageSegment(
                    type="markdown",
                    data={"text": markdown["text"], "title": str(markdown.get("title", ""))},
                )
            )
    raise OpsMeshError(
        "UNSUPPORTED_FEATURE", "DingTalk currently accepts text and markdown messages"
    )


def encode_message(message: OpsMeshMessage) -> tuple[str, dict[str, str]]:
    if message.segments and all(
        segment.type == "text" and isinstance(segment.data.get("text"), str) for segment in message
    ):
        content = message.extract_plain_text()
        if content:
            return "sampleText", {"content": content}
    if len(message.segments) == 1 and message.segments[0].type == "markdown":
        data = message.segments[0].data
        text = data.get("text")
        if isinstance(text, str) and text:
            title = data.get("title")
            return "sampleMarkdown", {
                "title": title if isinstance(title, str) and title else "OpsMesh",
                "text": text,
            }
    raise OpsMeshError(
        "UNSUPPORTED_FEATURE", "DingTalk sends nonempty text or one markdown segment"
    )
