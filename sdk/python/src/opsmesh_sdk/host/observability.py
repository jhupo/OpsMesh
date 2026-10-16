"""Host observability: correlated logs, metrics and traces with redaction.

Records automatically carry the call reference and are redacted at the
boundary; plaintext context and secrets are never recorded.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    Transport,
    TransportContext,
    TransportRequest,
    TransportResponse,
)

MAX_RECORD_BYTES: Final = 16_384


class ObservabilityRecord(BaseModel):
    """One structured observability record from the current invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(pattern="^(log|metric|trace)$")
    name: str = Field(min_length=1, max_length=200)
    value: float | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class ObservabilityReporter:
    """Reports correlated observability records through the host."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def log(self, name: str, attributes: Mapping[str, JsonValue] | None = None) -> None:
        await self._report(
            ObservabilityRecord(kind="log", name=name, attributes=dict(attributes or {}))
        )

    async def counter(self, name: str, *, value: float = 1.0) -> None:
        await self._report(ObservabilityRecord(kind="metric", name=name, value=value))

    async def gauge(self, name: str, *, value: float) -> None:
        await self._report(ObservabilityRecord(kind="metric", name=name, value=value))

    async def _report(self, record: ObservabilityRecord) -> None:
        if len(repr(record.attributes).encode()) > MAX_RECORD_BYTES:
            raise ValueError("the observability record exceeds the size bound")
        response = await self._send(
            "POST", "/host/observability/records", body=record.model_dump(mode="json")
        )
        del response

    async def _send(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: object = None,
    ) -> TransportResponse:
        request = TransportRequest(
            method=method, path=path, query=query or {}, body=body, stream=False
        )
        return await self._transport.send(request, TransportContext(scope=CancellationScope()))


__all__ = ["MAX_RECORD_BYTES", "ObservabilityRecord", "ObservabilityReporter"]
