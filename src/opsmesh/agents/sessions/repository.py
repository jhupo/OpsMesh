from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session as DbSession

from opsmesh.agents.sessions.models import (
    PERSISTENT_AGENT_SESSION_STATUSES,
    AgentSessionMetadata,
    SDKSessionMessageRecord,
)


class AgentSessionRepository:
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
    ) -> list[AgentSessionMetadata]:
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
                    AgentSessionMetadata.updated_at.desc(),
                    AgentSessionMetadata.id,
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

    def get_session(self, *, workspace_id: UUID, session_id: UUID) -> AgentSessionMetadata | None:
        return self._db_session.scalar(
            select(AgentSessionMetadata).where(
                AgentSessionMetadata.workspace_id == workspace_id,
                AgentSessionMetadata.id == session_id,
            )
        )

    def list_team_agent_sessions(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        agent_profile_id: UUID,
    ) -> list[AgentSessionMetadata]:
        return list(
            self._db_session.scalars(
                select(AgentSessionMetadata).where(
                    AgentSessionMetadata.workspace_id == workspace_id,
                    AgentSessionMetadata.agent_team_id == team_id,
                    AgentSessionMetadata.agent_profile_id == agent_profile_id,
                    AgentSessionMetadata.scope_type == "team_agent",
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
    ) -> list[SDKSessionMessageRecord]:
        return list(
            self._db_session.scalars(
                select(SDKSessionMessageRecord)
                .join(
                    AgentSessionMetadata,
                    SDKSessionMessageRecord.session_id == AgentSessionMetadata.session_key,
                )
                .where(
                    AgentSessionMetadata.workspace_id == workspace_id,
                    AgentSessionMetadata.id == session_id,
                )
                .order_by(SDKSessionMessageRecord.id.asc())
                .offset(non_negative_offset(offset))
                .limit(bounded_limit(limit))
            ).all()
        )

    def item_count(self, workspace_id: UUID, session_id: UUID) -> int:
        return int(
            self._db_session.scalar(
                select(func.count(SDKSessionMessageRecord.id))
                .join(
                    AgentSessionMetadata,
                    SDKSessionMessageRecord.session_id == AgentSessionMetadata.session_key,
                )
                .where(
                    AgentSessionMetadata.workspace_id == workspace_id,
                    AgentSessionMetadata.id == session_id,
                )
            )
            or 0
        )

    def latest_item(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> SDKSessionMessageRecord | None:
        return self._db_session.scalar(
            select(SDKSessionMessageRecord)
            .join(
                AgentSessionMetadata,
                SDKSessionMessageRecord.session_id == AgentSessionMetadata.session_key,
            )
            .where(
                AgentSessionMetadata.workspace_id == workspace_id,
                AgentSessionMetadata.id == session_id,
            )
            .order_by(SDKSessionMessageRecord.id.desc())
            .limit(1)
        )

    def delete_items(self, *, workspace_id: UUID, session_id: UUID) -> int:
        session = self._db_session.scalar(
            select(AgentSessionMetadata)
            .where(
                AgentSessionMetadata.workspace_id == workspace_id,
                AgentSessionMetadata.id == session_id,
            )
            .with_for_update()
        )
        if session is None:
            return 0
        item_count = self.item_count(workspace_id, session_id)
        self._db_session.execute(
            delete(SDKSessionMessageRecord).where(
                SDKSessionMessageRecord.session_id == session.session_key
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


def _session_filter_statement(
    *,
    workspace_id: UUID,
    agent_profile_id: UUID | None,
    agent_team_id: UUID | None,
    task_id: UUID | None,
    status: str | None,
) -> Select[tuple[AgentSessionMetadata]]:
    if status is not None:
        validate_status(status)
    statement = select(AgentSessionMetadata).where(
        AgentSessionMetadata.workspace_id == workspace_id
    )
    if agent_profile_id is not None:
        statement = statement.where(AgentSessionMetadata.agent_profile_id == agent_profile_id)
    if agent_team_id is not None:
        statement = statement.where(AgentSessionMetadata.agent_team_id == agent_team_id)
    if task_id is not None:
        statement = statement.where(AgentSessionMetadata.task_id == task_id)
    if status is not None:
        statement = statement.where(AgentSessionMetadata.status == status)
    return statement
