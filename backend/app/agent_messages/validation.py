from uuid import UUID

from sqlalchemy import select

from backend.app.agent_messages.contracts import MailboxStore
from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agents.models import AgentProfile
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam


class AgentMailboxValidationMixin(MailboxStore):
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
