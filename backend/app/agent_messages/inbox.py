from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from backend.app.agent_messages.constants import PENDING_STATUSES, READ_STATUSES
from backend.app.agent_messages.contracts import MailboxStore
from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_messages.queries import scoped_message_statement


class AgentMailboxInboxMixin(MailboxStore):
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
        message_statement = select(AgentMessage.thread_id).where(
            AgentMessage.workspace_id == workspace_id,
            (
                AgentMessage.sender_agent_profile_id == agent_profile_id
            )
            | (AgentMessage.recipient_agent_profile_id == agent_profile_id),
        )
        message_statement = scoped_message_statement(
            message_statement,
            thread_id=thread_id,
            task_id=task_id,
        )
        total = self._session.scalar(
            select(func.count()).select_from(message_statement.distinct().subquery())
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
        statement = scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
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
        statement = scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
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
        statement = scoped_message_statement(statement, thread_id=thread_id, task_id=task_id)
        total = self._session.scalar(statement)
        return int(total or 0)
