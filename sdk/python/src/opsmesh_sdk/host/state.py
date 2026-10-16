"""Host state: namespaced reads, writes, compare-and-swap and batches.

State lives in the host-assigned namespace. A batch may carry approved event
intents and is persisted with its state changes in one transaction; atomicity
never crosses the delegation boundary on its own.
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

MAX_BATCH_ENTRIES: Final = 256


class StateEntry(BaseModel):
    """One namespaced state value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=512)
    value: JsonValue = None
    expected_version: int | None = Field(default=None, ge=0)
    delete: bool = False


class StateBatch(BaseModel):
    """A transactional batch of state changes with optional event intents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[StateEntry, ...] = Field(min_length=1, max_length=MAX_BATCH_ENTRIES)
    event_drafts: tuple[Mapping[str, JsonValue], ...] = ()


class StateStore:
    """The host state client for the current invocation."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def get(self, key: str) -> JsonValue:
        response = await self._send("GET", f"/host/state/{key}")
        return response.body

    async def list(self, *, prefix: str = "", cursor: str | None = None) -> dict[str, JsonValue]:
        response = await self._send(
            "GET", "/host/state", query={"prefix": prefix, "cursor": cursor or ""}
        )
        body = response.body
        return body if isinstance(body, dict) else {}

    async def set(self, key: str, value: JsonValue) -> None:
        await self._send("PUT", f"/host/state/{key}", body={"value": value})

    async def compare_and_swap(self, key: str, value: JsonValue, *, expected_version: int) -> bool:
        response = await self._send(
            "POST",
            f"/host/state/{key}/cas",
            body={"value": value, "expected_version": expected_version},
        )
        body = response.body
        return bool(body.get("swapped")) if isinstance(body, dict) else False

    async def submit_batch(self, batch: StateBatch) -> None:
        await self._send("POST", "/host/state/batch", body=batch.model_dump(mode="json"))

    async def capacity(self) -> dict[str, JsonValue]:
        response = await self._send("GET", "/host/state/capacity")
        body = response.body
        return body if isinstance(body, dict) else {}

    async def _send(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: JsonValue = None,
    ) -> TransportResponse:
        request = TransportRequest(
            method=method, path=path, query=query or {}, body=body, stream=False
        )
        return await self._transport.send(request, TransportContext(scope=CancellationScope()))


__all__ = ["MAX_BATCH_ENTRIES", "StateBatch", "StateEntry", "StateStore"]
