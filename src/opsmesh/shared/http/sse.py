"""SSE framing with the same public-payload redaction as ordinary responses."""

import json

from fastapi.encoders import jsonable_encoder

from opsmesh.shared.security.redaction import redact_sensitive_payload


def sse_event(event_name: str, payload: dict[str, object], *, event_id: str | None = None) -> str:
    data = json.dumps(
        jsonable_encoder(redact_sensitive_payload(payload)), ensure_ascii=False, sort_keys=True
    )
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event_name}\ndata: {data}\n\n"
