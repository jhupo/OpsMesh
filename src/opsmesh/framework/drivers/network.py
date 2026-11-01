"""HTTP and WebSocket clients/servers sharing one process driver lifecycle."""

from opsmesh.framework.drivers.server import ServerDriver

from .httpx import HTTPXDriver
from .websockets import WebSocketDriver


class OpsMeshNetworkDriver(HTTPXDriver, WebSocketDriver, ServerDriver):
    """Cooperative constructors initialize OpsMeshDriver exactly once.

    HTTPX keyword arguments are forwarded to its managed AsyncClient. WebSocket
    connections use the same driver state, task supervisor and shutdown registry.
    """

    capabilities: frozenset[str] = frozenset(
        {"http_client", "websocket_client", "http_server", "websocket_server"}
    )
