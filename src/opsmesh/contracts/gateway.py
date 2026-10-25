"""Task-scoped gateway access. Routing and transport secrets stay in the host."""

from typing import Any, Protocol


class Channel(Protocol):
    async def reply(
        self, text: str = "", *, content: list[dict[str, Any]] | None = None, key: str
    ) -> str:
        """Queue a durable reply to this invocation's conversation."""
        ...

    async def request(self, operation: str, parameters: dict[str, Any], *, key: str) -> str:
        """Queue a native operation; requires a currently authorized platform admin."""
        ...
