"""Transport package: request execution, authentication and stream reading.

The transport executes calls and propagates identity. It contains no business
rules and never imports the generated API clients.
"""

from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    StreamChunk,
    Transport,
    TransportRequest,
    TransportResponse,
)
from opsmesh_sdk.transport.host import HostTransport
from opsmesh_sdk.transport.http import HttpTransport
from opsmesh_sdk.transport.streams import EventStreamReader, SseEvent

__all__ = [
    "CancellationScope",
    "EventStreamReader",
    "HostTransport",
    "HttpTransport",
    "SseEvent",
    "StreamChunk",
    "Transport",
    "TransportRequest",
    "TransportResponse",
]
