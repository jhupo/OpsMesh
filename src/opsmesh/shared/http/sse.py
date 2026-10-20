"""SSE framing with the same public-payload redaction as ordinary responses."""

from opsmesh.shared.security.redaction import RedactionRules, redact_sensitive_payload
from opsmesh.shared.serialization import canonical_json


def sse_event(
    event_name: str,
    payload: dict[str, object],
    *,
    rules: RedactionRules,
    event_id: str | None = None,
) -> str:
    if any(character in event_name for character in "\r\n\x00") or (
        event_id is not None and any(character in event_id for character in "\r\n\x00")
    ):
        raise ValueError("SSE field values must not contain line breaks or NUL")
    data = canonical_json(redact_sensitive_payload(payload, rules=rules))
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event_name}\ndata: {data}\n\n"
