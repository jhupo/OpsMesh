from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.agent_messages import (
    AgentMessageCreateRequest,
    AgentMessageThreadCreateRequest,
)
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam

T = TypeVar("T")
READ_STATUSES = {"read"}
PENDING_STATUSES = {"pending", "queued", "waiting", "waiting_reply"}


class AgentMailboxService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
                        (
                            AgentMessage.sender_agent_profile_id == agent_profile_id
                        )
                        | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
                    )
                )
            )
        return self._page(statement.order_by(AgentMessageThread.updated_at.desc()), page)

    def create_thread(
        self,
        workspace_id: UUID,
        data: AgentMessageThreadCreateRequest,
    ) -> AgentMessageThread:
        task_team_id = (
            self._task_team_id(workspace_id, data.task_id) if data.task_id is not None else None
        )
        agent_team_id = data.agent_team_id or task_team_id
        if agent_team_id is not None:
            self._require_team(workspace_id, agent_team_id)
        if data.task_id is not None and task_team_id is None and agent_team_id is not None:
            raise ValueError("Thread task is not assigned to a team")
        if task_team_id is not None and agent_team_id is not None and task_team_id != agent_team_id:
            raise ValueError("Thread task does not match team")
        payload = data.model_dump(exclude={"agent_team_id"})
        thread = AgentMessageThread(
            workspace_id=workspace_id,
            agent_team_id=agent_team_id,
            **payload,
        )
        self._session.add(thread)
        self._session.flush([thread])
        self._session.refresh(thread)
        return thread

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
    ) -> dict[str, object]:
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
        message_statement = _scoped_message_statement(
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

    def create_message(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        data: AgentMessageCreateRequest,
    ) -> AgentMessage:
        thread = self._require_thread(workspace_id, thread_id)
        self._require_agent(workspace_id, data.sender_agent_profile_id)
        self._require_agent(workspace_id, data.recipient_agent_profile_id)

        task_id = data.task_id if data.task_id is not None else thread.task_id
        task_team_id = self._task_team_id(workspace_id, task_id) if task_id is not None else None
        agent_team_id = data.agent_team_id or thread.agent_team_id or task_team_id
        if (
            thread.task_id is not None
            and data.task_id is not None
            and data.task_id != thread.task_id
        ):
            raise ValueError("Message task does not match thread")
        if (
            thread.agent_team_id is not None
            and data.agent_team_id is not None
            and data.agent_team_id != thread.agent_team_id
        ):
            raise ValueError("Message team does not match thread")
        if task_id is not None:
            self._require_task(workspace_id, task_id)
        if agent_team_id is not None:
            self._require_team(workspace_id, agent_team_id)
        if task_id is not None and task_team_id is None and agent_team_id is not None:
            raise ValueError("Message task is not assigned to a team")
        if task_team_id is not None and agent_team_id is not None and task_team_id != agent_team_id:
            raise ValueError("Message task does not match team")
        if data.reply_to_message_id is not None:
            self._require_message(workspace_id, thread_id, data.reply_to_message_id)

        payload = data.model_dump(exclude={"task_id", "agent_team_id"})
        message = AgentMessage(
            workspace_id=workspace_id,
            thread_id=thread_id,
            task_id=task_id,
            agent_team_id=agent_team_id,
            **payload,
        )
        self._session.add(message)
        self._session.flush()
        thread.updated_at = message.created_at
        self._session.flush([message, thread])
        self._session.refresh(message)
        return message

    def mark_message_read(
        self,
        workspace_id: UUID,
        message_id: UUID,
        *,
        read_at: datetime | None = None,
    ) -> AgentMessage | None:
        message = self._session.scalar(
            select(AgentMessage).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.id == message_id,
            )
        )
        if message is None:
            return None
        message.status = "read"
        message.read_at = read_at or datetime.now(UTC)
        self._session.flush([message])
        self._session.refresh(message)
        return message

    def set_thread_status(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        *,
        status: str,
    ) -> AgentMessageThread | None:
        if status not in {"active", "closed", "archived"}:
            raise ValueError("Unsupported agent message thread status")
        thread = self._session.scalar(
            select(AgentMessageThread).where(
                AgentMessageThread.workspace_id == workspace_id,
                AgentMessageThread.id == thread_id,
            )
        )
        if thread is None:
            return None
        thread.status = status
        thread.updated_at = datetime.now(UTC)
        self._session.flush([thread])
        self._session.refresh(thread)
        return thread

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

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)

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

    def _agent_thread_count(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
        *,
        thread_id: UUID | None = None,
        task_id: UUID | None = None,
    ) -> int:
        message_statement = select(AgentMessage.thread_id).where(
            AgentMessage.workspace_id == workspace_id,
            (
                AgentMessage.sender_agent_profile_id == agent_profile_id
            )
            | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
        )
        message_statement = _scoped_message_statement(
            message_statement,
            thread_id=thread_id,
            task_id=task_id,
        )
        total = self._session.scalar(
            select(func.count()).select_from(
                message_statement.distinct().subquery()
            )
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
        statement = _scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        total = self._session.scalar(statement)
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
        statement = _scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        total = self._session.scalar(statement)
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
        statement = _scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        total = self._session.scalar(statement)
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

    def _require_thread(self, workspace_id: UUID, thread_id: UUID) -> AgentMessageThread:
        thread = self._session.scalar(
            select(AgentMessageThread).where(
                AgentMessageThread.workspace_id == workspace_id,
                AgentMessageThread.id == thread_id,
            )
        )
        if thread is None:
            raise ValueError("Agent message thread not found")
        return thread

    def _require_agent(self, workspace_id: UUID, agent_profile_id: UUID) -> None:
        agent_id = self._session.scalar(
            select(AgentProfile.id).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_profile_id,
            )
        )
        if agent_id is None:
            raise ValueError("Agent not found in workspace")

    def _require_task(self, workspace_id: UUID, task_id: UUID) -> None:
        task = self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            raise ValueError("Task not found")

    def _require_team(self, workspace_id: UUID, agent_team_id: UUID) -> None:
        team = self._session.scalar(
            select(AgentTeam.id).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == agent_team_id,
            )
        )
        if team is None:
            raise ValueError("Team not found")

    def _task_team_id(self, workspace_id: UUID, task_id: UUID) -> UUID | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            raise ValueError("Task not found")
        return task.agent_team_id

    def _require_message(self, workspace_id: UUID, thread_id: UUID, message_id: UUID) -> None:
        message = self._session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.thread_id == thread_id,
                AgentMessage.id == message_id,
            )
        )
        if message is None:
            raise ValueError("Reply target message not found")


def _scoped_message_statement(
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
