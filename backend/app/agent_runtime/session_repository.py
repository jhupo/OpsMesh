from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DbSession

from backend.app.agent_runtime.sessions import (
    PERSISTENT_AGENT_SESSION_STATUSES,
    PersistentAgentSession,
    PersistentAgentSessionItem,
)


class PersistentSessionRepository:
    def __init__(self, db_session: DbSession) -> None:
        self._db_session = db_session

    def list_sessions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID | None,
        agent_team_id: UUID | None,
        task_id: UUID | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[PersistentAgentSession]:
        statement = _session_filter_statement(
            workspace_id=workspace_id,
            agent_profile_id=agent_profile_id,
            agent_team_id=agent_team_id,
            task_id=task_id,
            status=status,
        )
        return list(
            self._db_session.scalars(
                statement.order_by(
                    PersistentAgentSession.updated_at.desc(),
                    PersistentAgentSession.id,
                )
                .offset(non_negative_offset(offset))
                .limit(bounded_limit(limit))
            ).all()
        )

    def count_sessions(
        self,
        *,
        workspace_id: UUID,
        agent_profile_id: UUID | None,
        agent_team_id: UUID | None,
        task_id: UUID | None,
        status: str | None,
    ) -> int:
        statement = select(func.count()).select_from(
            _session_filter_statement(
                workspace_id=workspace_id,
                agent_profile_id=agent_profile_id,
                agent_team_id=agent_team_id,
                task_id=task_id,
                status=status,
            ).subquery()
        )
        return int(self._db_session.scalar(statement) or 0)

    def get_session(self, *, workspace_id: UUID, session_id: UUID) -> PersistentAgentSession | None:
        return self._db_session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == workspace_id,
                PersistentAgentSession.id == session_id,
            )
        )

    def list_team_agent_sessions(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        agent_profile_id: UUID,
    ) -> list[PersistentAgentSession]:
        return list(
            self._db_session.scalars(
                select(PersistentAgentSession).where(
                    PersistentAgentSession.workspace_id == workspace_id,
                    PersistentAgentSession.agent_team_id == team_id,
                    PersistentAgentSession.agent_profile_id == agent_profile_id,
                    PersistentAgentSession.scope_type == "team_agent",
                )
            )
        )

    def list_items(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
        limit: int,
        offset: int,
    ) -> list[PersistentAgentSessionItem]:
        return list(
            self._db_session.scalars(
                select(PersistentAgentSessionItem)
                .where(
                    PersistentAgentSessionItem.workspace_id == workspace_id,
                    PersistentAgentSessionItem.persistent_session_id == session_id,
                )
                .order_by(PersistentAgentSessionItem.sequence.asc())
                .offset(non_negative_offset(offset))
                .limit(bounded_limit(limit))
            ).all()
        )

    def all_items(
        self,
        *,
        workspace_id: UUID,
        session_id: UUID,
    ) -> list[PersistentAgentSessionItem]:
        return list(
            self._db_session.scalars(
                select(PersistentAgentSessionItem)
                .where(
                    PersistentAgentSessionItem.workspace_id == workspace_id,
                    PersistentAgentSessionItem.persistent_session_id == session_id,
                )
                .order_by(PersistentAgentSessionItem.sequence.asc())
            ).all()
        )

    def item_count(self, workspace_id: UUID, session_id: UUID) -> int:
        return int(
            self._db_session.scalar(
                select(func.count(PersistentAgentSessionItem.id)).where(
                    PersistentAgentSessionItem.workspace_id == workspace_id,
                    PersistentAgentSessionItem.persistent_session_id == session_id,
                )
            )
            or 0
        )

    def latest_item(
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

    def delete_items(self, *, workspace_id: UUID, session_id: UUID) -> int:
        item_count = self.item_count(workspace_id, session_id)
        self._db_session.execute(
            delete(PersistentAgentSessionItem).where(
                PersistentAgentSessionItem.workspace_id == workspace_id,
                PersistentAgentSessionItem.persistent_session_id == session_id,
            )
        )
        return item_count


def validate_status(status: str) -> None:
    if status not in PERSISTENT_AGENT_SESSION_STATUSES:
        allowed = ", ".join(sorted(PERSISTENT_AGENT_SESSION_STATUSES))
        raise ValueError(
            f"Invalid persistent agent session status: {status}. Expected one of {allowed}"
        )


def bounded_limit(value: int) -> int:
    return min(max(value, 1), 500)


def non_negative_offset(value: int) -> int:
    return max(value, 0)


def positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _session_filter_statement(
    *,
    workspace_id: UUID,
    agent_profile_id: UUID | None,
    agent_team_id: UUID | None,
    task_id: UUID | None,
    status: str | None,
):
    if status is not None:
        validate_status(status)
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
    return statement
