"""Reconstruct ordered messages from the channels SSE response protocol."""

import json


def frames(response):
    """Decode framed UTF-8 SSE data without accepting partial events."""
    event = None
    data = []
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield event or "message", json.loads("\n".join(data))
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        raise ValueError("Incomplete SSE event")


def collect_stream(response, on_delta=None):
    """Keep message boundaries and require ordered deltas plus final done."""
    messages = []
    active = {}
    seen = set()
    completed = None
    for kind, payload in frames(response):
        if not isinstance(payload, dict) or completed is not None:
            raise ValueError("Invalid SSE response")
        identifier = payload.get("message_id")
        if kind in {"message", "message_start"}:
            if (
                not isinstance(identifier, str)
                or not identifier
                or identifier in seen
            ):
                raise ValueError("Invalid message identifier")
            seen.add(identifier)
            if kind == "message":
                messages.append(
                    {
                        "content": payload.get("content"),
                        "data": payload.get("data"),
                    }
                )
            else:
                message = {
                    "content": [{"type": "text", "text": ""}],
                    "data": None,
                }
                messages.append(message)
                active[identifier] = (message, 0)
        elif kind == "message_delta":
            if identifier not in active or not isinstance(
                payload.get("delta"), str
            ):
                raise ValueError("Unmatched message increment")
            message, sequence = active[identifier]
            if (
                type(payload.get("sequence")) is not int
                or payload["sequence"] != sequence + 1
            ):
                raise ValueError("Out-of-order message increment")
            message["content"][0]["text"] += payload["delta"]
            active[identifier] = (message, sequence + 1)
            if on_delta is not None:
                on_delta(payload["delta"])
        elif kind == "message_end":
            if identifier not in active:
                raise ValueError("Unmatched message end")
            del active[identifier]
        elif kind == "error":
            return {
                "conversation_id": payload.get("conversation_id"),
                "status": "failed",
                "messages": messages,
                "error": payload,
            }
        elif kind == "done":
            if active or payload.get("status") not in {"completed", "waiting"}:
                raise ValueError("Stream completed with an unfinished message")
            completed = {**payload, "messages": messages, "error": None}
        else:
            raise ValueError("Unknown SSE event")
    if completed is None:
        raise ValueError("Stream ended before request completion")
    return completed
