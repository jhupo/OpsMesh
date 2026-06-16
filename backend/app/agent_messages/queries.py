from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.api.pagination import PageParams

T = TypeVar("T")


class AgentMailboxQueryMixin:
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
