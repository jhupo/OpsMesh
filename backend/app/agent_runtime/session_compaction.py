from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session as DbSession

from backend.app.agent_runtime.session_repository import (
    PersistentSessionRepository,
    non_negative_int,
    positive_int,
)
from backend.app.agent_runtime.session_views import (
    PersistentSessionCompactionResult,
    session_item_metadata,
)
from backend.app.agent_runtime.sessions import (
    ACTIVE_SESSION_STATUS,
    PersistentAgentSession,
    PersistentAgentSessionItem,
)


class PersistentSessionCompactionService:
    def __init__(self, db_session: DbSession, repository: PersistentSessionRepository) -> None:
        self._db_session = db_session
        self._repository = repository

    def compact_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        fold_first_n: int,
        keep_recent_m: int,
        summary_role: str = "developer",
    ) -> PersistentSessionCompactionResult | None:
        _validate_compaction_window(fold_first_n, keep_recent_m, summary_role)
        session = self._repository.get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return None
        items = self._repository.all_items(workspace_id=workspace_id, session_id=session.id)
        item_count = len(items)
        if item_count <= keep_recent_m:
            return _unchanged_compaction_result(session.id, item_count)
        if fold_first_n + keep_recent_m > item_count:
            raise ValueError("fold_first_n and keep_recent_m overlap for this session")
        if fold_first_n >= item_count and keep_recent_m > 0:
            return _unchanged_compaction_result(session.id, item_count)

        folded = items[:fold_first_n]
        retained = items[-keep_recent_m:] if keep_recent_m else []
        self._replace_items_with_summary(
            workspace_id=workspace_id,
            session=session,
            folded=folded,
            retained=retained,
            summary_role=summary_role,
        )
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
        session = self._repository.get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None or session.status != ACTIVE_SESSION_STATUS:
            return None
        max_items = positive_int(policy.get("session_max_items"))
        if max_items is None:
            return None
        item_count = self._repository.item_count(workspace_id, session.id)
        if item_count <= max_items:
            return None
        keep_recent = non_negative_int(policy.get("session_keep_recent_items"))
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

    def _replace_items_with_summary(
        self,
        *,
        workspace_id: UUID,
        session: PersistentAgentSession,
        folded: list[PersistentAgentSessionItem],
        retained: list[PersistentAgentSessionItem],
        summary_role: str,
    ) -> None:
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
        for row in folded + retained:
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


def _validate_compaction_window(
    fold_first_n: int,
    keep_recent_m: int,
    summary_role: str,
) -> None:
    if fold_first_n < 1:
        raise ValueError("fold_first_n must be greater than zero")
    if keep_recent_m < 0:
        raise ValueError("keep_recent_m must be zero or greater")
    if summary_role not in {"system", "developer"}:
        raise ValueError("summary_role must be system or developer")


def _unchanged_compaction_result(
    session_id: UUID,
    item_count: int,
) -> PersistentSessionCompactionResult:
    return PersistentSessionCompactionResult(
        session_id=session_id,
        folded_item_count=0,
        retained_item_count=item_count,
        summary_sequence=None,
        item_count=item_count,
    )


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
        metadata = session_item_metadata(row)
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
