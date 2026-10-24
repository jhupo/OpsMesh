from abc import ABC, abstractmethod
from asyncio import Event
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChannelEvent:
    id: str
    type: str
    external_user_id: str | None = None
    conversation_id: str | None = None
    text: str = ""
    is_message: bool = False
    scope: str = "private"
    external_role: str | None = None
    reply_target: dict[str, Any] | None = None
    content: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutgoingMessage:
    id: str
    conversation_id: str
    text: str
    reply_to: str | None = None
    scope: str = "private"
    sequence: int = 1
    target: dict[str, Any] | None = None
    content: list[dict[str, Any]] = field(default_factory=list)


EventSink = Callable[[ChannelEvent], Awaitable[None]]


class OperationRejected(Exception):
    """A permanent rejection with a safe public code, before any ambiguous side effect."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ChannelAdapter(ABC):
    def __init__(self) -> None:
        self.ready = Event()

    @abstractmethod
    async def connect(self, sink: EventSink) -> None: ...

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> Any: ...

    @property
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """JSON capability catalogue, without credentials or connection state."""
        ...

    @abstractmethod
    async def execute(self, operation: str, parameters: dict[str, Any]) -> Any:
        """Execute an explicitly authorized native operation in the gateway worker."""
        ...

    @abstractmethod
    async def close(self) -> None: ...
