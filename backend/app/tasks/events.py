from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.tasks.timeline import TaskTimelineService


class TaskEventFeedService:
    """Build a cursor-based task event feed from the canonical task timeline."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_feed(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, object] | None:
        timeline = TaskTimelineService(self._session).get_timeline(
            workspace_id=workspace_id,
            task_id=task_id,
            limit=max(after_cursor + limit + 1, 2_000),
        )
        if timeline is None:
            return None

        events = _list(timeline.get("events"))
        timeline_summary = _dict(timeline.get("summary"))
        truncated_count = _int(timeline_summary.get("truncated_count"))
        feed_items = [
            _feed_event(event, cursor=truncated_count + index)
            for index, event in enumerate(events, start=1)
            if truncated_count + index > after_cursor
        ]
        returned = feed_items[:limit]
        next_cursor = _next_cursor(returned, after_cursor)
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "summary": {
                "total_events": _int(timeline_summary.get("total_events")),
                "returned_events": len(returned),
                "after_cursor": after_cursor,
                "next_cursor": next_cursor,
                "has_more": bool(feed_items[limit:]),
                "poll_after_seconds": 1,
                "truncated_before_cursor": truncated_count,
                "source_counts": timeline_summary.get("source_counts", {}),
                "phase_counts": timeline_summary.get("phase_counts", {}),
            },
            "events": returned,
        }


def _feed_event(event: object, *, cursor: int) -> dict[str, object]:
    payload = _dict(event)
    return {
        **payload,
        "cursor": cursor,
        "event_id": _event_id(payload, cursor),
    }


def _event_id(event: dict[str, object], cursor: int) -> str:
    occurred_at = event.get("occurred_at")
    if isinstance(occurred_at, datetime):
        occurred = occurred_at.isoformat()
    else:
        occurred = str(occurred_at or "")
    parts = [
        str(event.get("source_type") or "event"),
        str(event.get("event_type") or "unknown"),
        str(event.get("agent_run_id") or ""),
        str(event.get("task_step_id") or ""),
        str(event.get("artifact_id") or ""),
        str(event.get("sequence") or cursor),
        occurred,
    ]
    return ":".join(parts)


def _next_cursor(events: list[dict[str, object]], default: int) -> int:
    if not events:
        return default
    cursor = events[-1].get("cursor")
    return cursor if isinstance(cursor, int) else default


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
