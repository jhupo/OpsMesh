"""One host-owned adapter per configured platform connection."""

from abc import ABC, abstractmethod
from asyncio import Event as Readiness
from typing import Any

from .errors import OperationRejected
from .schemas import Capabilities, ConnectionState, Event, EventSink, Message, Receipt, Result


class Adapter(ABC):
    def __init__(self) -> None:
        self.ready = Readiness()
        self.state: ConnectionState = "stopped"
        self.sink: EventSink | None = None

    def set_state(self, state: ConnectionState) -> None:
        self.state = state
        if state == "connected":
            self.ready.set()
        else:
            self.ready.clear()

    def require_ready(self) -> None:
        if not self.ready.is_set():
            raise OperationRejected("channel_not_connected")

    async def publish(self, event: Event) -> None:
        if self.sink is None:
            raise RuntimeError("Event received outside the channel lifetime")
        await self.sink(event)

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Supported operations and configuration; never credentials or live state."""
        ...

    @abstractmethod
    async def run(self, sink: EventSink) -> None:
        """Own reception and reconnects until cancelled; release all resources on exit."""
        ...

    @abstractmethod
    async def send(self, message: Message) -> Receipt:
        """Return evidence of platform acceptance, not proof of user delivery."""
        ...

    @abstractmethod
    async def call(self, operation: str, parameters: dict[str, Any]) -> Result:
        """Execute only an operation explicitly declared in capabilities."""
        ...
