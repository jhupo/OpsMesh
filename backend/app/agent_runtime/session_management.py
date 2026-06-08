from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from backend.app.agent_runtime.sessions import (
    ACTIVE_SESSION_STATUS,
    ARCHIVED_SESSION_STATUS,
    FROZEN_SESSION_STATUS,
    PERSISTENT_AGENT_SESSION_STATUSES,
    PersistentAgentSession,
    PersistentAgentSessionItem,
)
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text


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


class PersistentAgentSessionManagementService:
    """Workspace-scoped controls for persistent OpenAI Agents SDK sessions."""

    def __init__(self, db_session: DbSession) -> None:
        self._db_session = db_session

    def list_sessions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID | None = None,
        agent_team_id: UUID | None = None,
        task_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PersistentSessionSummary]:
        if status is not None:
            _validate_status(status)
        statement = select(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace_id
        )
        if agent_profile_id is not None:
            statement = statement.where(PersistentAgentSession.agent_profile_id == agent_profile_id)
        if agent_team_id is not None:
            statement = statement.where(PersistentAgentSession.agent_team_id == agent_team_id)
        if task_id is not None:
            statement = statement.where(PersistentAgentSession.task_id == task_id)
        if status is not None:
            statement = statement.where(PersistentAgentSession.status == status)
        rows = self._db_session.scalars(
            statement.order_by(PersistentAgentSession.updated_at.desc(), PersistentAgentSession.id)
            .offset(_non_negative_offset(offset))
            .limit(_bounded_limit(limit))
        ).all()
        return [self._summary_for_session(row) for row in rows]

    def count_sessions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID | None = None,
        agent_team_id: UUID | None = None,
        task_id: UUID | None = None,
        status: str | None = None,
    ) -> int:
        if status is not None:
            _validate_status(status)
        statement = select(func.count()).select_from(PersistentAgentSession).where(
            PersistentAgentSession.workspace_id == workspace_id
        )
        if agent_profile_id is not None:
            statement = statement.where(PersistentAgentSession.agent_profile_id == agent_profile_id)
        if agent_team_id is not None:
            statement = statement.where(PersistentAgentSession.agent_team_id == agent_team_id)
        if task_id is not None:
            statement = statement.where(PersistentAgentSession.task_id == task_id)
        if status is not None:
            statement = statement.where(PersistentAgentSession.status == status)
        return int(self._db_session.scalar(statement) or 0)

    def get_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        item_limit: int = 50,
        item_offset: int = 0,
    ) -> PersistentSessionDetail | None:
        session = self._get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return None
        return PersistentSessionDetail(
            session=self._summary_for_session(session),
            items=self.list_items(
                workspace_id=workspace_id,
                session_id=session_id,
                limit=item_limit,
                offset=item_offset,
            ),
            item_limit=_bounded_limit(item_limit),
            item_offset=_non_negative_offset(item_offset),
        )

    def list_items(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PersistentSessionItemView]:
        session = self._get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return []
        rows = self._db_session.scalars(
            select(PersistentAgentSessionItem)
            .where(
                PersistentAgentSessionItem.workspace_id == workspace_id,
                PersistentAgentSessionItem.persistent_session_id == session.id,
            )
            .order_by(PersistentAgentSessionItem.sequence.asc())
            .offset(_non_negative_offset(offset))
            .limit(_bounded_limit(limit))
        ).all()
        return [_item_view(row) for row in rows]

    def clear_session_items(self, *, workspace_id: UUID, session_id: UUID) -> int:
        session = self._get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return 0
        deleted = self._delete_items(workspace_id=workspace_id, session_id=session.id)
        session.openai_conversation_id = None
        session.updated_at = datetime.now(UTC)
        self._db_session.flush()
        return deleted

    def reset_team_agent_sessions(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        agent_profile_id: UUID,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> list[PersistentSessionSummary]:
        sessions = list(
            self._db_session.scalars(
                select(PersistentAgentSession).where(
                    PersistentAgentSession.workspace_id == workspace_id,
                    PersistentAgentSession.agent_team_id == team_id,
                    PersistentAgentSession.agent_profile_id == agent_profile_id,
                    PersistentAgentSession.scope_type == "team_agent",
                )
            )
        )
        reset_at = datetime.now(UTC)
        summaries: list[PersistentSessionSummary] = []
        for session in sessions:
            self._delete_items(workspace_id=workspace_id, session_id=session.id)
            session.openai_conversation_id = None
            session.status = ACTIVE_SESSION_STATUS
            session.updated_at = reset_at
            session.session_metadata = {
                **dict(session.session_metadata or {}),
                "last_reset": {
                    "reason": redact_sensitive_text(reason),
                    "reset_at": reset_at.isoformat(),
                    "metadata": redact_sensitive_payload(dict(metadata or {})),
                },
            }
            self._db_session.flush([session])
            summaries.append(self._summary_for_session(session))
        return summaries

    def archive_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
    ) -> PersistentSessionSummary | None:
        return self.set_session_status(
            workspace_id=workspace_id,
            session_id=session_id,
            status=ARCHIVED_SESSION_STATUS,
        )

    def freeze_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
    ) -> PersistentSessionSummary | None:
        return self.set_session_status(
            workspace_id=workspace_id,
            session_id=session_id,
            status=FROZEN_SESSION_STATUS,
        )

    def activate_session(
        self, *, workspace_id: UUID, session_id: UUID
    ) -> PersistentSessionSummary | None:
        return self.set_session_status(
            workspace_id=workspace_id,
            session_id=session_id,
            status=ACTIVE_SESSION_STATUS,
        )

    def set_session_status(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        status: str,
    ) -> PersistentSessionSummary | None:
        _validate_status(status)
        session = self._get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return None
        session.status = status
        session.updated_at = datetime.now(UTC)
        self._db_session.flush([session])
        return self._summary_for_session(session)

    def compact_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        fold_first_n: int,
        keep_recent_m: int,
        summary_role: str = "developer",
    ) -> PersistentSessionCompactionResult | None:
        if fold_first_n < 1:
            raise ValueError("fold_first_n must be greater than zero")
        if keep_recent_m < 0:
            raise ValueError("keep_recent_m must be zero or greater")
        if summary_role not in {"system", "developer"}:
            raise ValueError("summary_role must be system or developer")

        session = self._get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return None
        items = list(
            self._db_session.scalars(
                select(PersistentAgentSessionItem)
                .where(
                    PersistentAgentSessionItem.workspace_id == workspace_id,
                    PersistentAgentSessionItem.persistent_session_id == session.id,
                )
                .order_by(PersistentAgentSessionItem.sequence.asc())
            ).all()
        )
        item_count = len(items)
        if item_count <= keep_recent_m:
            return PersistentSessionCompactionResult(
                session_id=session.id,
                folded_item_count=0,
                retained_item_count=item_count,
                summary_sequence=None,
                item_count=item_count,
            )
        if fold_first_n + keep_recent_m > item_count:
            raise ValueError("fold_first_n and keep_recent_m overlap for this session")
        if fold_first_n >= item_count and keep_recent_m > 0:
            return PersistentSessionCompactionResult(
                session_id=session.id,
                folded_item_count=0,
                retained_item_count=item_count,
                summary_sequence=None,
                item_count=item_count,
            )

        folded = items[:fold_first_n]
        retained = items[-keep_recent_m:] if keep_recent_m else []
        summary_item = PersistentAgentSessionItem(
            workspace_id=workspace_id,
            persistent_session_id=session.id,
            sequence=1,
            item=_compaction_summary_item(
                role=summary_role,
                session=session,
                folded=folded,
                retained_count=len(retained),
            ),
            created_at=datetime.now(UTC),
        )
        for row in items:
            self._db_session.delete(row)
        self._db_session.flush()

        self._db_session.add(summary_item)
        for sequence, row in enumerate(retained, start=2):
            self._db_session.add(
                PersistentAgentSessionItem(
                    workspace_id=workspace_id,
                    persistent_session_id=session.id,
                    sequence=sequence,
                    item=dict(row.item),
                    created_at=row.created_at,
                )
            )
        session.updated_at = datetime.now(UTC)
        self._db_session.flush()
        return PersistentSessionCompactionResult(
            session_id=session.id,
            folded_item_count=len(folded),
            retained_item_count=len(retained),
            summary_sequence=1,
            item_count=1 + len(retained),
        )

    def compact_if_needed(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        policy: dict[str, object],
    ) -> PersistentSessionCompactionResult | None:
        if policy.get("auto_compact_enabled") is not True:
            return None

        session = self._get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None or session.status != ACTIVE_SESSION_STATUS:
            return None

        max_items = _positive_int(policy.get("session_max_items"))
        if max_items is None:
            return None
        item_count = self._item_count(workspace_id, session.id)
        if item_count <= max_items:
            return None

        keep_recent = _non_negative_int(policy.get("session_keep_recent_items"))
        if keep_recent is None:
            keep_recent = max(max_items - 1, 0)
        keep_recent = min(keep_recent, max(max_items - 1, 0))
        summary_role = policy.get("summary_role")
        if summary_role not in {"system", "developer"}:
            summary_role = "developer"

        return self.compact_session(
            workspace_id=workspace_id,
            session_id=session.id,
            fold_first_n=item_count - keep_recent,
            keep_recent_m=keep_recent,
            summary_role=str(summary_role),
        )

    def _get_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
    ) -> PersistentAgentSession | None:
        return self._db_session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == workspace_id,
                PersistentAgentSession.id == session_id,
            )
        )

    def _summary_for_session(self, session: PersistentAgentSession) -> PersistentSessionSummary:
        item_count = self._item_count(session.workspace_id, session.id)
        latest_item = self._latest_item(session.workspace_id, session.id)
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
            latest_item_metadata=_item_metadata(latest_item) if latest_item is not None else None,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    def _item_count(self, workspace_id: UUID, session_id: UUID) -> int:
        return (
            self._db_session.scalar(
                select(func.count(PersistentAgentSessionItem.id)).where(
                    PersistentAgentSessionItem.workspace_id == workspace_id,
                    PersistentAgentSessionItem.persistent_session_id == session_id,
                )
            )
            or 0
        )

    def _latest_item(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> PersistentAgentSessionItem | None:
        return self._db_session.scalar(
            select(PersistentAgentSessionItem)
            .where(
                PersistentAgentSessionItem.workspace_id == workspace_id,
                PersistentAgentSessionItem.persistent_session_id == session_id,
            )
            .order_by(PersistentAgentSessionItem.sequence.desc())
            .limit(1)
        )

    def _delete_items(self, *, workspace_id: UUID, session_id: UUID) -> int:
        item_count = self._item_count(workspace_id, session_id)
        self._db_session.execute(
            delete(PersistentAgentSessionItem).where(
                PersistentAgentSessionItem.workspace_id == workspace_id,
                PersistentAgentSessionItem.persistent_session_id == session_id,
            )
        )
        return item_count


def _item_view(row: PersistentAgentSessionItem) -> PersistentSessionItemView:
    return PersistentSessionItemView(
        id=row.id,
        sequence=row.sequence,
        item=dict(row.item),
        created_at=row.created_at,
        metadata=_item_metadata(row),
    )


def _item_metadata(row: PersistentAgentSessionItem) -> dict[str, Any]:
    item = row.item
    content = item.get("content")
    return {
        "id": str(row.id),
        "sequence": row.sequence,
        "created_at": row.created_at.isoformat(),
        "role": item.get("role"),
        "type": item.get("type"),
        "content_type": type(content).__name__ if content is not None else None,
        "content_preview": _content_preview(content),
    }


def _compaction_summary_item(
    *,
    role: str,
    session: PersistentAgentSession,
    folded: list[PersistentAgentSessionItem],
    retained_count: int,
) -> dict[str, Any]:
    lines = [
        "Persistent session deterministic compaction.",
        f"session_key: {session.session_key}",
        f"scope: {session.scope_type}/{session.scope_id}",
        f"folded_items: {len(folded)}",
        f"retained_following_items: {retained_count}",
        "folded_item_metadata:",
    ]
    for row in folded:
        metadata = _item_metadata(row)
        lines.append(
            "- "
            f"sequence={metadata['sequence']} "
            f"role={metadata['role'] or 'unknown'} "
            f"type={metadata['type'] or 'unknown'} "
            f"preview={metadata['content_preview'] or ''}"
        )
    return {
        "role": role,
        "content": "\n".join(lines),
        "metadata": {
            "kind": "deterministic_compaction",
            "folded_item_count": len(folded),
            "folded_sequences": [row.sequence for row in folded],
            "retained_following_item_count": retained_count,
            "compacted_at": datetime.now(UTC).isoformat(),
        },
    }


def _content_preview(content: object, *, max_chars: int = 160) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(_content_preview(part, max_chars=max_chars) or "" for part in content)
    elif isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or content)
    else:
        text = str(content)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _bounded_limit(value: int) -> int:
    return min(max(value, 1), 500)


def _non_negative_offset(value: int) -> int:
    return max(value, 0)


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _validate_status(status: str) -> None:
    if status not in PERSISTENT_AGENT_SESSION_STATUSES:
        allowed = ", ".join(sorted(PERSISTENT_AGENT_SESSION_STATUSES))
        raise ValueError(
            f"Invalid persistent agent session status: {status}. Expected one of {allowed}"
        )
