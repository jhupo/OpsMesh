from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session as DbSession

from backend.app.agent_runtime.session_repository import (
    PersistentSessionRepository,
    bounded_limit,
    non_negative_offset,
    validate_status,
)
from backend.app.agent_runtime.session_views import (
    PersistentSessionDetail,
    PersistentSessionItemView,
    PersistentSessionSummary,
    session_item_view,
    session_summary,
)
from backend.app.agent_runtime.sessions import (
    ACTIVE_SESSION_STATUS,
    ARCHIVED_SESSION_STATUS,
    FROZEN_SESSION_STATUS,
)
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text


class PersistentAgentSessionManagementService:
    """Workspace-scoped controls for persistent OpenAI Agents SDK sessions."""

    def __init__(self, db_session: DbSession) -> None:
        self._db_session = db_session
        self._repository = PersistentSessionRepository(db_session)

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
        rows = self._repository.list_sessions(
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            agent_team_id=agent_team_id,
            task_id=task_id,
            status=status,
            limit=limit,
            offset=offset,
        )
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
        return self._repository.count_sessions(
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            agent_team_id=agent_team_id,
            task_id=task_id,
            status=status,
        )

    def get_session(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        item_limit: int = 50,
        item_offset: int = 0,
    ) -> PersistentSessionDetail | None:
        session = self._repository.get_session(workspace_id=workspace_id, session_id=session_id)
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
            item_limit=bounded_limit(item_limit),
            item_offset=non_negative_offset(item_offset),
        )

    def list_items(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PersistentSessionItemView]:
        session = self._repository.get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return []
        rows = self._repository.list_items(
            workspace_id=workspace_id,
            session_id=session.id,
            limit=limit,
            offset=offset,
        )
        return [session_item_view(row) for row in rows]

    def clear_session_items(self, *, workspace_id: UUID, session_id: UUID) -> int:
        session = self._repository.get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return 0
        deleted = self._repository.delete_items(workspace_id=workspace_id, session_id=session.id)
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
        sessions = self._repository.list_team_agent_sessions(
            workspace_id=workspace_id,
            team_id=team_id,
            agent_profile_id=agent_profile_id,
        )
        reset_at = datetime.now(UTC)
        summaries: list[PersistentSessionSummary] = []
        for session in sessions:
            self._repository.delete_items(workspace_id=workspace_id, session_id=session.id)
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
        validate_status(status)
        session = self._repository.get_session(workspace_id=workspace_id, session_id=session_id)
        if session is None:
            return None
        session.status = status
        session.updated_at = datetime.now(UTC)
        self._db_session.flush([session])
        return self._summary_for_session(session)

    def _summary_for_session(self, session) -> PersistentSessionSummary:
        return session_summary(
            session,
            item_count=self._repository.item_count(session.workspace_id, session.id),
            latest_item=self._repository.latest_item(session.workspace_id, session.id),
        )
