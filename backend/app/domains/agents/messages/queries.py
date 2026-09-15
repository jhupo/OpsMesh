from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select

from backend.app.core.common.pagination import PageParams
from backend.app.domains.agents.messages.contracts import AgentInbox
from backend.app.domains.agents.messages.models import (
    PENDING_STATUSES,
    READ_STATUSES,
    AgentMessage,
    AgentMessageThread,
)

T = TypeVar("T")


class AgentMailboxQueries:
    """Workspace-scoped mailbox reads, inbox projections and summary rollups."""

    def list_threads(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        task_id: UUID | None = None,
        agent_team_id: UUID | None = None,
        agent_profile_id: UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[AgentMessageThread], int]:
        task_team_id = self._task_team_id(workspace_id, task_id) if task_id is not None else None
        if agent_team_id is not None:
            self._require_team(workspace_id, agent_team_id)
        if task_id is not None and task_team_id is None and agent_team_id is not None:
            raise ValueError("Thread task is not assigned to a team")
        if task_team_id is not None and agent_team_id is not None and task_team_id != agent_team_id:
            raise ValueError("Thread task does not match team")
        if agent_profile_id is not None:
            self._require_agent(workspace_id, agent_profile_id)

        statement = select(AgentMessageThread).where(
            AgentMessageThread.workspace_id == workspace_id,
        )
        if task_id is not None:
            statement = statement.where(AgentMessageThread.task_id == task_id)
        if agent_team_id is not None:
            statement = statement.where(AgentMessageThread.agent_team_id == agent_team_id)
        if status is not None:
            statement = statement.where(AgentMessageThread.status == status)
        if agent_profile_id is not None:
            statement = statement.where(
                AgentMessageThread.id.in_(
                    select(AgentMessage.thread_id).where(
                        AgentMessage.workspace_id == workspace_id,
                        (AgentMessage.sender_agent_profile_id == agent_profile_id)
                        | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
                    )
                )
            )
        return self._page(statement.order_by(AgentMessageThread.updated_at.desc()), page)

    def list_messages(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        page: PageParams,
        *,
        recipient_agent_profile_id: UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[AgentMessage], int]:
        self._require_thread(workspace_id, thread_id)
        if recipient_agent_profile_id is not None:
            self._require_agent(workspace_id, recipient_agent_profile_id)

        statement = select(AgentMessage).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.thread_id == thread_id,
        )
        if recipient_agent_profile_id is not None:
            statement = statement.where(
                AgentMessage.recipient_agent_profile_id == recipient_agent_profile_id,
            )
        if status is not None:
            statement = statement.where(AgentMessage.status == status)
        return self._page(
            statement.order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc()),
            page,
        )

    def get_agent_inbox(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        *,
        latest_limit: int = 20,
        unread_only: bool = False,
        thread_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> AgentInbox:
        self._require_agent(workspace_id, agent_profile_id)
        if thread_id is not None:
            thread = self._require_thread(workspace_id, thread_id)
            if task_id is None:
                task_id = thread.task_id
            elif thread.task_id != task_id:
                raise ValueError("Inbox task does not match thread")
        if task_id is not None:
            self._require_task(workspace_id, task_id)
        limit = max(latest_limit, 0)
        message_statement = select(AgentMessage).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.recipient_agent_profile_id == agent_profile_id,
        )
        message_statement = scoped_message_statement(
            message_statement,
            thread_id=thread_id,
            task_id=task_id,
        )
        if unread_only:
            message_statement = message_statement.where(
                AgentMessage.read_at.is_(None),
                AgentMessage.status.notin_(READ_STATUSES),
            )
        latest_messages = list(
            self._session.scalars(
                message_statement.order_by(
                    AgentMessage.created_at.desc(),
                    AgentMessage.id.desc(),
                ).limit(limit)
            ).all()
        )
        return {
            "workspace_id": workspace_id,
            "agent_profile_id": agent_profile_id,
            "generated_at": datetime.now(UTC),
            "thread_count": self._agent_thread_count(
                workspace_id,
                agent_profile_id,
                thread_id=thread_id,
                task_id=task_id,
            ),
            "message_count": self._agent_message_count(
                workspace_id,
                agent_profile_id,
                thread_id=thread_id,
                task_id=task_id,
            ),
            "unread_count": self._agent_unread_count(
                workspace_id,
                agent_profile_id,
                thread_id=thread_id,
                task_id=task_id,
            ),
            "pending_count": self._agent_pending_count(
                workspace_id,
                agent_profile_id,
                thread_id=thread_id,
                task_id=task_id,
            ),
            "latest_messages": latest_messages,
        }

    def _agent_thread_count(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        *,
        thread_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> int:
        statement = select(AgentMessage.thread_id).where(
            AgentMessage.workspace_id == workspace_id,
            (AgentMessage.sender_agent_profile_id == agent_profile_id)
            | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
        )
        statement = scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        total = self._session.scalar(
            select(func.count()).select_from(statement.distinct().subquery())
        )
        return int(total or 0)

    def _agent_message_count(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        *,
        thread_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> int:
        statement = select(func.count()).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.recipient_agent_profile_id == agent_profile_id,
        )
        total = self._session.scalar(
            scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        )
        return int(total or 0)

    def _agent_unread_count(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        *,
        thread_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> int:
        statement = select(func.count()).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.recipient_agent_profile_id == agent_profile_id,
            AgentMessage.read_at.is_(None),
            AgentMessage.status.notin_(READ_STATUSES),
        )
        total = self._session.scalar(
            scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        )
        return int(total or 0)

    def _agent_pending_count(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        *,
        thread_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> int:
        statement = select(func.count()).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.recipient_agent_profile_id == agent_profile_id,
            AgentMessage.status.in_(PENDING_STATUSES),
        )
        total = self._session.scalar(
            scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        )
        return int(total or 0)

    def get_summary(self, workspace_id: UUID, *, latest_limit: int = 20) -> dict[str, object]:
        limit = max(latest_limit, 0)
        return {
            "workspace_id": workspace_id,
            "generated_at": datetime.now(UTC),
            "thread_count": self._count_threads(workspace_id),
            "message_count": self._count_messages(workspace_id),
            "unread_count": self._count_unread_messages(workspace_id),
            "pending_count": self._count_pending_messages(workspace_id),
            "thread_status_counts": self._thread_status_counts(workspace_id),
            "message_status_counts": self._message_status_counts(workspace_id),
            "latest_messages_by_task": self._latest_messages_by_task(workspace_id, limit),
            "latest_messages_by_team": self._latest_messages_by_team(workspace_id, limit),
        }

    def _count_threads(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(AgentMessageThread.workspace_id == workspace_id)
        )
        return int(total or 0)

    def _count_messages(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(AgentMessage.workspace_id == workspace_id)
        )
        return int(total or 0)

    def _count_unread_messages(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.read_at.is_(None),
                AgentMessage.status.notin_(READ_STATUSES),
            )
        )
        return int(total or 0)

    def _count_pending_messages(self, workspace_id: UUID) -> int:
        total = self._session.scalar(
            select(func.count()).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.status.in_(PENDING_STATUSES),
            )
        )
        return int(total or 0)

    def _thread_status_counts(self, workspace_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(AgentMessageThread.status, func.count())
            .where(AgentMessageThread.workspace_id == workspace_id)
            .group_by(AgentMessageThread.status)
        )
        return {str(status): int(count) for status, count in rows}

    def _message_status_counts(self, workspace_id: UUID) -> dict[str, int]:
        rows = self._session.execute(
            select(AgentMessage.status, func.count())
            .where(AgentMessage.workspace_id == workspace_id)
            .group_by(AgentMessage.status)
        )
        return {str(status): int(count) for status, count in rows}

    def _latest_messages_by_task(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> list[dict[str, object]]:
        if limit == 0:
            return []
        rank = func.row_number().over(
            partition_by=AgentMessage.task_id,
            order_by=(AgentMessage.created_at.desc(), AgentMessage.id.desc()),
        )
        ranked = (
            select(AgentMessage.id.label("message_id"), rank.label("rank"))
            .where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.task_id.is_not(None),
            )
            .subquery()
        )
        rows = self._session.execute(
            select(AgentMessage)
            .join(ranked, ranked.c.message_id == AgentMessage.id)
            .where(ranked.c.rank == 1)
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(limit)
        )
        return [
            {"task_id": message.task_id, "message": message}
            for message in rows.scalars()
            if message.task_id is not None
        ]

    def _latest_messages_by_team(
        self,
        workspace_id: UUID,
        limit: int,
    ) -> list[dict[str, object]]:
        if limit == 0:
            return []
        rank = func.row_number().over(
            partition_by=AgentMessage.agent_team_id,
            order_by=(AgentMessage.created_at.desc(), AgentMessage.id.desc()),
        )
        ranked = (
            select(
                AgentMessage.id.label("message_id"),
                AgentMessage.agent_team_id.label("team_id"),
                rank.label("rank"),
            )
            .where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id.is_not(None),
            )
            .subquery()
        )
        rows = self._session.execute(
            select(AgentMessage, ranked.c.team_id)
            .join(ranked, ranked.c.message_id == AgentMessage.id)
            .where(ranked.c.rank == 1)
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(limit)
        )
        return [{"team_id": team_id, "message": message} for message, team_id in rows]


def scoped_message_statement(
    statement: Select[tuple[T]],
    *,
    thread_id: UUID | None,
    task_id: UUID | None,
) -> Select[tuple[T]]:
    if thread_id is not None:
        statement = statement.where(AgentMessage.thread_id == thread_id)
    if task_id is not None:
        statement = statement.where(AgentMessage.task_id == task_id)
    return statement
