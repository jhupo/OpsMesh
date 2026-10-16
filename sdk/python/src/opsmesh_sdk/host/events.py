"""Host events: publish the package's own namespaced events with causal refs.

Publishing never forges platform facts. Consumption is queried through the
core ``orchestration.triggers`` subscription mechanism, not a second engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.extensions.events import EventEnvelope
from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    Transport,
    TransportContext,
    TransportRequest,
    TransportResponse,
)
from opsmesh_sdk.values import IdempotencyKey

MAX_SUMMARY_BYTES: Final = 65_536


class EventDraft(BaseModel):
    """A package's own event before the host stamps verified context fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: str = Field(min_length=1, max_length=200)
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    causal_refs: tuple[str, ...] = ()
    content_ref: str | None = Field(default=None, max_length=512)


class EventPublisher:
    """Publishes extension-owned events through the host."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def publish(self, draft: EventDraft, *, idempotency_key: IdempotencyKey) -> str:
        if len(repr(draft.summary).encode()) > MAX_SUMMARY_BYTES:
            raise ValueError("the event summary exceeds the declared size bound")
        response = await self._send(
            "POST",
            "/host/events/publish",
            body=draft.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("the host returned a malformed event receipt")
        event_id = body.get("event_id")
        if not isinstance(event_id, str):
            raise ValueError("the host returned a malformed event receipt")
        return event_id

    async def _send(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: object = None,
        idempotency_key: str | None = None,
    ) -> TransportResponse:
        request = TransportRequest(
            method=method,
            path=path,
            query=query or {},
            body=body,
            idempotency_key=idempotency_key,
            stream=False,
        )
        return await self._transport.send(request, TransportContext(scope=CancellationScope()))


__all__ = ["MAX_SUMMARY_BYTES", "EventDraft", "EventPublisher", "EventEnvelope"]
