from abc import ABC, abstractmethod
from asyncio import Event
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IncomingMessage:
    id: str
    external_user_id: str
    conversation_id: str
    text: str
    scope: str = "private"
    external_role: str | None = None
    reply_target: dict[str, Any] | None = None


@dataclass(frozen=True)
class OutgoingMessage:
    id: str
    conversation_id: str
    text: str
    reply_to: str | None = None
    scope: str = "private"
    sequence: int = 1
    target: dict[str, Any] | None = None


MessageSink = Callable[[IncomingMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    def __init__(self) -> None:
        self.ready = Event()

    @abstractmethod
    async def connect(self, sink: MessageSink) -> None: ...

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
