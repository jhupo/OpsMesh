from datetime import datetime
from typing import Protocol, TypedDict, TypeVar
from uuid import UUID

from sqlalchemy import Select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.core.pagination import PageParams

T = TypeVar("T")


class AgentInbox(TypedDict):
    workspace_id: UUID
    agent_profile_id: UUID
    generated_at: datetime
    thread_count: int
    message_count: int
    unread_count: int
    pending_count: int
    latest_messages: list[AgentMessage]


class MailboxStore(Protocol):
    _session: Session

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]: ...
    def _require_thread(self, workspace_id: UUID, thread_id: UUID) -> AgentMessageThread: ...
    def _require_agent(self, workspace_id: UUID, agent_profile_id: UUID | None) -> None: ...
    def _require_task(self, workspace_id: UUID, task_id: UUID) -> None: ...
    def _require_team(self, workspace_id: UUID, agent_team_id: UUID) -> None: ...
    def _task_team_id(self, workspace_id: UUID, task_id: UUID) -> UUID | None: ...
    def _require_message(self, workspace_id: UUID, thread_id: UUID, message_id: UUID) -> None: ...
