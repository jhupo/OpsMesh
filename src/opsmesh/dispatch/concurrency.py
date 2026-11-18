"""Explicit process-local concurrency control for event handlers."""

from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from opsmesh.events import Event

T = TypeVar("T")


class SessionBusyError(RuntimeError):
    """Another call owns this session; delivery must be retried."""


class SessionGuard(Generic[T]):
    """Reject overlapping session calls within one host-owned instance.

    Share one instance across deliveries that must exclude each other. It owns
    no global hooks or plugin state and provides no cross-process protection.
    Event sources must supply source-scoped session IDs. Busy calls raise
    instead of appearing successfully processed to a queue.
    """

    def __init__(self, handler: Callable[..., Awaitable[T]]) -> None:
        """Wrap a handler whose lifetime includes its dependency cleanup."""
        self._handler = handler
        self._active: set[str] = set()

    async def __call__(self, event: Event, **kwargs: Any) -> T:
        """Hold a session slot until completion, failure, or cancellation.

        Events explicitly lacking a session bypass the guard. Unexpected
        identity errors propagate. All overlapping calls, including reuse of
        the same event object, are rejected; recursive calls are not reentrant.
        """
        try:
            session_id = event.get_session_id()
        except (NotImplementedError, ValueError):
            return await self._handler(event, **kwargs)
        if session_id in self._active:
            raise SessionBusyError(
                "Another event is being processed in this session"
            )
        self._active.add(session_id)
        try:
            return await self._handler(event, **kwargs)
        finally:
            self._active.remove(session_id)
