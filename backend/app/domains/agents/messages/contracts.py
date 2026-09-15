from datetime import datetime
from typing import Any, Protocol, TypedDict, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import Select
from sqlalchemy.orm import Session

from backend.app.core.contracts import TimestampedModel
from backend.app.core.pagination import PageParams
from backend.app.core.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.domains.agents.messages.models import AgentMessage, AgentMessageThread

T = TypeVar("T")


class AgentMessageThreadCreatePayload(Protocol):
    task_id: UUID | None
    agent_team_id: UUID | None
    subject: str
    status: str

    def model_dump(self, *, exclude: set[str]) -> dict[str, Any]: ...


class AgentMessageCreatePayload(Protocol):
    task_id: UUID | None
    agent_team_id: UUID | None
    sender_agent_profile_id: UUID
    recipient_agent_profile_id: UUID
    reply_to_message_id: UUID | None

    def model_dump(self, *, exclude: set[str]) -> dict[str, Any]: ...


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


class AgentMessageThreadCreateRequest(BaseModel):
    task_id: UUID | None = None
    agent_team_id: UUID | None = None
    subject: str = Field(default="", max_length=240)
    status: str = Field(default="active", max_length=32)


class AgentMessageCreateRequest(BaseModel):
    task_id: UUID | None = None
    agent_team_id: UUID | None = None
    sender_agent_profile_id: UUID
    recipient_agent_profile_id: UUID
    reply_to_message_id: UUID | None = None
    message_type: str = Field(default="message", min_length=1, max_length=80)
    body: str = ""
    payload: dict[str, object] = Field(default_factory=dict)
    status: str = Field(default="sent", max_length=32)
    read_at: datetime | None = None


class AgentMessageMarkReadRequest(BaseModel):
    read_at: datetime | None = None


class AgentMessageThreadStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|closed|archived)$")


class AgentMessageThreadResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID | None
    agent_team_id: UUID | None
    subject: str
    status: str


class AgentMessageResponse(TimestampedModel):
    workspace_id: UUID
    thread_id: UUID
    task_id: UUID | None
    agent_team_id: UUID | None
    sender_agent_profile_id: UUID | None
    recipient_agent_profile_id: UUID | None
    reply_to_message_id: UUID | None
    message_type: str
    body: str
    payload: dict[str, object]
    status: str
    read_at: datetime | None

    @field_serializer("payload")
    def _serialize_payload(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)

    @field_serializer("body")
    def _serialize_body(self, value: str) -> str:
        return redact_sensitive_text(value)


class AgentMailboxLatestTaskMessage(BaseModel):
    task_id: UUID
    message: AgentMessageResponse


class AgentMailboxLatestTeamMessage(BaseModel):
    team_id: UUID
    message: AgentMessageResponse


class AgentMailboxSummaryResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    thread_count: int
    message_count: int
    unread_count: int
    pending_count: int
    thread_status_counts: dict[str, int]
    message_status_counts: dict[str, int]
    latest_messages_by_task: list[AgentMailboxLatestTaskMessage]
    latest_messages_by_team: list[AgentMailboxLatestTeamMessage]


class AgentInboxSummaryResponse(BaseModel):
    workspace_id: UUID
    agent_profile_id: UUID
    generated_at: datetime
    thread_count: int
    message_count: int
    unread_count: int
    pending_count: int
    latest_messages: list[AgentMessageResponse]
