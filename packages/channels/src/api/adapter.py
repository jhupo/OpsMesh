"""Framework adapter for the host's HTTP request and response transport."""

from typing import Any

from opsmesh.adapters import Adapter, Channel


class APIAdapter(Adapter):
    """Expose HTTP responses through the framework channel abstraction."""

    @classmethod
    def get_name(cls) -> str:
        """Return the HTTP channel protocol identifier."""
        return "api"

    async def _call(self, channel: Channel, operation: str, **data: Any) -> Any:
        if operation != "reply":
            raise ValueError(f"Unsupported API operation: {operation}")
        data["event"].reply(data["result"])
