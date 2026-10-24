"""Explicit, JSON-compatible progress events emitted by a plugin invocation."""

from typing import Any, Protocol


class Events(Protocol):
    async def emit(self, event: str, data: Any) -> int:
        """Persist a plugin event and return its task-local sequence number."""
        ...
