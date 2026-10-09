from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.app.agents.sessions.models import PersistentAgentSession, SDKAgentMessage


@dataclass(frozen=True)
class PersistentSessionItemView:
    id: int
    item: dict[str, Any]
    created_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PersistentSessionSummary:
    id: UUID
    workspace_id: UUID
    session_key: str
    scope_type: str
    scope_id: str
    status: str
    agent_profile_id: UUID | None
    agent_team_id: UUID | None
    task_id: UUID | None
    metadata: dict[str, Any]
    item_count: int
    latest_item_metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PersistentSessionDetail:
    session: PersistentSessionSummary
    items: list[PersistentSessionItemView]
    item_limit: int
    item_offset: int


def session_item_view(row: SDKAgentMessage) -> PersistentSessionItemView:
    return PersistentSessionItemView(
        id=row.id,
        item=json.loads(row.message_data),
        created_at=row.created_at,
        metadata=session_item_metadata(row),
    )


def session_summary(
    session: PersistentAgentSession,
    *,
    item_count: int,
    latest_item: SDKAgentMessage | None,
) -> PersistentSessionSummary:
    return PersistentSessionSummary(
        id=session.id,
        workspace_id=session.workspace_id,
        session_key=session.session_key,
        scope_type=session.scope_type,
        scope_id=session.scope_id,
        status=session.status,
        agent_profile_id=session.agent_profile_id,
        agent_team_id=session.agent_team_id,
        task_id=session.task_id,
        metadata=dict(session.session_metadata),
        item_count=item_count,
        latest_item_metadata=(
            session_item_metadata(latest_item) if latest_item is not None else None
        ),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def session_item_metadata(row: SDKAgentMessage) -> dict[str, Any]:
    item = json.loads(row.message_data)
    content = item.get("content")
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat(),
        "role": item.get("role"),
        "type": item.get("type"),
        "content_type": type(content).__name__ if content is not None else None,
        "content_preview": content_preview(content),
    }


def content_preview(content: object, *, max_chars: int = 160) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(content_preview(part, max_chars=max_chars) or "" for part in content)
    elif isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or content)
    else:
        text = str(content)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."
