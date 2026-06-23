from datetime import datetime

RUNNING_LEASE_STATUSES = {"running"}
LIFECYCLE_EVENTS_LIMIT = 50


def append_worker_lifecycle_events(
    metadata: dict[str, object],
    events: list[dict[str, object]],
) -> dict[str, object]:
    existing = metadata.get("lifecycle_events")
    lifecycle_events = list(existing) if isinstance(existing, list) else []
    lifecycle_events.extend(events)
    metadata["lifecycle_events"] = lifecycle_events[-LIFECYCLE_EVENTS_LIMIT:]
    if events:
        metadata["last_lifecycle_event"] = events[-1]
    return metadata


def worker_lifecycle_event(
    event_type: str,
    at: datetime,
    *,
    attempt: int,
    status: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": event_type,
        "at": at.isoformat(),
        "attempt": attempt,
    }
    if status is not None:
        event["status"] = status
    if metadata:
        event.update(metadata)
    return event


def worker_finish_lifecycle_event(status: str) -> str:
    if status == "retrying":
        return "requeued"
    if status == "failed":
        return "failed"
    if status == "expired":
        return "expired"
    return "completed"
