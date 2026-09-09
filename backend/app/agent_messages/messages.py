from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from backend.app.agent_messages.contracts import MailboxStore
from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_messages.payloads import message_create_payload
from backend.app.api.schemas.agent_messages import AgentMessageCreateRequest


class AgentMailboxMessageMixin(MailboxStore):
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

        message = AgentMessage(
            workspace_id=workspace_id,
            thread_id=thread_id,
            task_id=task_id,
            agent_team_id=agent_team_id,
            **message_create_payload(data),
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
