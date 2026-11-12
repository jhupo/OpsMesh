"""Explicit process-local concurrency control for event handlers."""

from collections.abc import Awaitable, Callable

from opsmesh.adapters import Channel, Event


class SessionBusyError(RuntimeError):
    """Another call owns this channel's session; delivery must be retried."""


class SessionGuard:
    """Reject overlapping session calls within one host-owned instance.

    Share one instance across deliveries that must exclude each other. It owns
    no global hooks or plugin state and provides no cross-process protection.
    Busy calls raise instead of appearing successfully processed to a queue.
    """

    def __init__(
        self, handler: Callable[[Channel, Event], Awaitable[None]]
    ) -> None:
        """Wrap a handler whose lifetime includes its dependency cleanup."""
        self._handler = handler
        self._active: set[tuple[str, str, str]] = set()

    async def __call__(self, channel: Channel, event: Event) -> None:
        """Hold a session slot until completion, failure, or cancellation.

        Events explicitly lacking a session bypass the guard. Unexpected
        identity errors propagate. All overlapping calls, including reuse of
        the same event object, are rejected; recursive calls are not reentrant.
        """
        try:
            session_id = event.get_session_id()
        except (NotImplementedError, ValueError):
            await self._handler(channel, event)
            return
        identity = (channel.type, channel.self_id, session_id)
        if identity in self._active:
            raise SessionBusyError(
                "Another event is being processed in this session"
            )
        self._active.add(identity)
        try:
            await self._handler(channel, event)
        finally:
            self._active.remove(identity)
