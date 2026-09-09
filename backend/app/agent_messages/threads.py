from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from backend.app.agent_messages.constants import THREAD_STATUSES
from backend.app.agent_messages.contracts import MailboxStore
from backend.app.agent_messages.models import AgentMessageThread
from backend.app.agent_messages.payloads import thread_create_payload
from backend.app.api.schemas.agent_messages import AgentMessageThreadCreateRequest


class AgentMailboxThreadMixin(MailboxStore):
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
        thread = AgentMessageThread(
            workspace_id=workspace_id,
            agent_team_id=agent_team_id,
            **thread_create_payload(data),
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
