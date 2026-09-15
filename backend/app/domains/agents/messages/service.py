from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.app.core.common.pagination import PageParams
from backend.app.core.db.pagination import page_scalars
from backend.app.domains.agents.messages.contracts import (
    AgentMessageCreatePayload,
    AgentMessageThreadCreatePayload,
)
from backend.app.domains.agents.messages.models import (
    THREAD_STATUSES,
    AgentMessage,
    AgentMessageThread,
)
from backend.app.domains.agents.messages.queries import AgentMailboxQueries
from backend.app.domains.agents.models import AgentProfile
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.workspace.teams.models import AgentTeam

T = TypeVar("T")


class AgentMailboxService(AgentMailboxQueries):
    """Own mailbox commands and their workspace-scoped invariants."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)

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

    def _require_agent(self, workspace_id: UUID, agent_profile_id: UUID | None) -> None:
        agent_id = self._session.scalar(
            select(AgentProfile.id).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id == agent_profile_id,
            )
        )
        if agent_id is None:
            raise ValueError("Agent not found in workspace")

    def _require_task(self, workspace_id: UUID, task_id: UUID) -> None:
        task_id_value = self._session.scalar(
            select(Task.id).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task_id_value is None:
            raise ValueError("Task not found")

    def _require_team(self, workspace_id: UUID, agent_team_id: UUID) -> None:
        team_id = self._session.scalar(
            select(AgentTeam.id).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == agent_team_id,
            )
        )
        if team_id is None:
            raise ValueError("Team not found")

    def _task_team_id(self, workspace_id: UUID, task_id: UUID) -> UUID | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            raise ValueError("Task not found")
        return task.agent_team_id

    def _require_message(self, workspace_id: UUID, thread_id: UUID, message_id: UUID) -> None:
        message_id_value = self._session.scalar(
            select(AgentMessage.id).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.thread_id == thread_id,
                AgentMessage.id == message_id,
            )
        )
        if message_id_value is None:
            raise ValueError("Reply target message not found")

    def create_thread(
        self,
        workspace_id: UUID,
        data: AgentMessageThreadCreatePayload,
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
        thread = AgentMessageThread(
            workspace_id=workspace_id,
            agent_team_id=agent_team_id,
            **data.model_dump(exclude={"agent_team_id"}),
        )
        self._session.add(thread)
        self._session.flush([thread])
        self._session.refresh(thread)
        return thread

    def set_thread_status(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        *,
        status: str,
    ) -> AgentMessageThread | None:
        if status not in THREAD_STATUSES:
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

    def create_message(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        data: AgentMessageCreatePayload,
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

        message = AgentMessage(
            workspace_id=workspace_id,
            thread_id=thread_id,
            task_id=task_id,
            agent_team_id=agent_team_id,
            **data.model_dump(exclude={"task_id", "agent_team_id"}),
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
