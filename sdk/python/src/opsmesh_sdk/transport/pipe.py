"""Bounded serial host RPC over the isolated worker's private stdio channel."""

import asyncio
import json
import sys
from collections.abc import AsyncIterator

from opsmesh_sdk.errors import TransportError
from opsmesh_sdk.transport.contracts import (
    StreamChunk,
    TransportContext,
    TransportRequest,
    TransportResponse,
)

MAX_MESSAGE_BYTES = 2 * 1048576


class PipeTransport:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._output = sys.stdout.buffer
        self._input = sys.stdin.buffer

    async def send(self, request: TransportRequest, context: TransportContext) -> TransportResponse:
        context.scope.ensure_active()
        if request.stream or request.content is not None:
            raise TransportError("The invocation pipe accepts bounded JSON requests only")
        async with self._lock:
            self._sequence += 1
            payload = (
                json.dumps(
                    {
                        "type": "request",
                        "id": self._sequence,
                        "request": request.model_dump(mode="json"),
                    },
                    allow_nan=False,
                ).encode()
                + b"\n"
            )
            if len(payload) > MAX_MESSAGE_BYTES:
                raise TransportError("Host RPC request exceeds the byte bound")
            self._output.write(payload)
            self._output.flush()
            response = await asyncio.to_thread(self._input.readline, MAX_MESSAGE_BYTES + 1)
            if not response.endswith(b"\n") or len(response) > MAX_MESSAGE_BYTES:
                raise TransportError("Invalid bounded host RPC response")
            value = json.loads(response)
            if value.get("id") != self._sequence:
                raise TransportError("Host RPC response occurrence differs")
            result = TransportResponse.model_validate(
                {key: item for key, item in value.items() if key != "id"}
            )
            if result.status >= 400:
                raise TransportError("The host denied or could not confirm this operation")
            return result

    async def stream(
        self,
        request: TransportRequest,
        context: TransportContext,
    ) -> AsyncIterator[StreamChunk]:
        raise TransportError("Invocation host streams are not supported")
        yield StreamChunk()  # pragma: no cover

    async def aclose(self) -> None:
        pass
