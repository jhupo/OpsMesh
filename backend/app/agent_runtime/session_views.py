from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.app.agent_runtime.sessions import (
    PersistentAgentSession,
    PersistentAgentSessionItem,
)


@dataclass(frozen=True)
class PersistentSessionItemView:
    id: UUID
    sequence: int
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
    openai_conversation_id: str | None
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


@dataclass(frozen=True)
class PersistentSessionCompactionResult:
    session_id: UUID
    folded_item_count: int
    retained_item_count: int
    summary_sequence: int | None
    item_count: int


def session_item_view(row: PersistentAgentSessionItem) -> PersistentSessionItemView:
    return PersistentSessionItemView(
        id=row.id,
        sequence=row.sequence,
        item=dict(row.item),
        created_at=row.created_at,
        metadata=session_item_metadata(row),
    )


def session_summary(
    session: PersistentAgentSession,
    *,
    item_count: int,
    latest_item: PersistentAgentSessionItem | None,
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
        openai_conversation_id=session.openai_conversation_id,
        metadata=dict(session.session_metadata),
        item_count=item_count,
        latest_item_metadata=(
            session_item_metadata(latest_item) if latest_item is not None else None
        ),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def session_item_metadata(row: PersistentAgentSessionItem) -> dict[str, Any]:
    item = row.item
    content = item.get("content")
    return {
        "id": str(row.id),
        "sequence": row.sequence,
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
