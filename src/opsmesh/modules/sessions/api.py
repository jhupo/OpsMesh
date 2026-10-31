"""User-facing conversations, distinct from model history and checkpoints."""

import builtins
from datetime import datetime
from typing import Literal, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ConversationSession(OpsMeshContract):
    id: str
    owner_id: str
    agent_ref: str
    workspace_id: str | None = None
    status: Literal["open", "closed"] = "open"
    active_run_id: str | None = None
    created_at: datetime
    revision: int = 1


class ConversationMessage(OpsMeshContract):
    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    text: str
    run_id: str | None = None
    attachments: tuple[str, ...] = ()
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class Conversations(Protocol):
    async def open(
        self, owner_id: str, agent_ref: str, workspace_id: str | None = None
    ) -> ConversationSession: ...
    async def get(self, session_id: str) -> ConversationSession: ...
    async def get_or_open(
        self, key: str, owner_id: str, agent_ref: str, workspace_id: str | None = None
    ) -> ConversationSession: ...
    async def list(self, owner_id: str | None = None) -> list[ConversationSession]: ...
    async def claim(self, session_id: str, run_id: str) -> None: ...
    async def active_claims(self) -> dict[str, str]: ...
    async def release(self, session_id: str, run_id: str) -> None: ...
    async def close(self, session_id: str) -> ConversationSession: ...
    async def append(
        self,
        session_id: str,
        role: Literal["user", "assistant", "system"],
        text: str,
        *,
        message_id: str | None = None,
        run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ConversationMessage: ...
    async def history(self, session_id: str) -> builtins.list[ConversationMessage]: ...
