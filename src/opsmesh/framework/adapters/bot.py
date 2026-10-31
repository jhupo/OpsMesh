"""Public connected bot abstraction, independent of account persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from opsmesh.framework.errors import OpsMeshError

from .event import OpsMeshEvent
from .message import OpsMeshMessage

if TYPE_CHECKING:
    from .adapter import OpsMeshAdapter


class BotRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    adapter_id: str
    bot_id: str


class ReplyTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    bot: BotRef
    channel_id: str
    thread_id: str | None = None
    message_id: str | None = None
    delivery_sequence: int = Field(default=1, ge=1, exclude_if=lambda value: value == 1)


class OpsMeshBot(ABC):
    def __init__(self, adapter: OpsMeshAdapter, self_id: str) -> None:
        self.adapter = adapter
        self.self_id = self_id

    @property
    def type(self) -> str:
        return self.adapter.name

    @property
    def ref(self) -> BotRef:
        return BotRef(adapter_id=self.type, bot_id=self.self_id)

    @abstractmethod
    async def send(self, target: OpsMeshEvent | ReplyTarget, message: OpsMeshMessage) -> Any:
        """Send through this protocol; unsupported segments must report errors."""

    async def reply(self, event: OpsMeshEvent, message: OpsMeshMessage | str) -> Any:
        return await self.send(
            event, message if isinstance(message, OpsMeshMessage) else OpsMeshMessage(message)
        )

    async def upload_file(self, target: ReplyTarget, name: str, content: bytes) -> str | None:
        raise OpsMeshError("UNSUPPORTED_FEATURE", "This adapter does not support file delivery")

    @abstractmethod
    async def call_api(self, api: str, **data: Any) -> Any:
        """Invoke a protocol-specific bot API."""
