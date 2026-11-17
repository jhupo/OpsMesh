"""Convert request content, and encode JSON envelopes and SSE frames."""

import json
from collections.abc import Callable
from typing import Any

from starlette.responses import Response

from ..attachments import Attachment, AttachmentCollection
from ..messages import Message, MessageSegment
from .errors import HTTPError
from .schemas import MessageRequest, TextContent

_JSON_TYPE = "application/json; charset=utf-8"


def encode_json(payload: Any) -> bytes:
    """Serialize a contract value without exposing Python objects."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_response(payload: Any, *, status_code: int = 200) -> Response:
    """Return one buffered JSON response."""
    return Response(
        status_code=status_code,
        headers={"content-type": _JSON_TYPE},
        content=encode_json(payload),
    )


def error_response(error: HTTPError) -> Response:
    """Map a safe request error to a JSON response."""
    return Response(
        status_code=error.status_code,
        headers={"content-type": _JSON_TYPE, **error.headers},
        content=encode_json({"error": error.message}),
    )


def sse_event(event: str, data: Any) -> bytes:
    """Encode one complete server-sent event frame."""
    if not event or "\n" in event:
        raise ValueError("SSE event names must be a single non-empty line")
    payload = encode_json(data)
    return b"event: " + event.encode("utf-8") + b"\ndata: " + payload + b"\n\n"


def from_request(
    request: MessageRequest,
    files: AttachmentCollection,
    resolve: Callable[[str, AttachmentCollection], Attachment] | None = None,
) -> Message:
    """Resolve each attachment under the receiving connection."""
    value = request.message
    if isinstance(value, str):
        return Message(value)
    message = Message()
    for part in value:
        if isinstance(part, TextContent):
            message.append(MessageSegment.text(part.text))
        else:
            if resolve is None:
                raise HTTPError(422, "Attachment transfer is unavailable")
            metadata = resolve(part.attachment_id, files)
            segment = MessageSegment.attachment(metadata)
            if segment.type != part.type and part.type != "file":
                raise HTTPError(422, "Attachment type does not match content")
            message.append(MessageSegment(part.type, segment.data))
    return message
