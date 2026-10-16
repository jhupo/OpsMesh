"""Host models: approved model and embedding operations with usage metering.

Calls go through the current connection contract and are metered. The host's
model interface serves approved external calls; the primary agent's
ModelProvider stays with execution, so there is no second runner here.
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
from opsmesh_sdk.values import ContentBlock, Usage

MAX_PROMPT_BLOCKS: Final = 512
MAX_PROMPT_BYTES: Final = 262_144


def _embedding_component(value: JsonValue) -> float:
    """Accept JSON numbers only; booleans and strings are a malformed answer."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("the host returned a malformed embedding answer")
    return float(value)


class ModelPrompt(BaseModel):
    """A model prompt as neutral content blocks, never a raw secret-bearing string."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocks: tuple[ContentBlock, ...] = Field(min_length=1, max_length=MAX_PROMPT_BLOCKS)
    max_output_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ModelResult(BaseModel):
    """A model answer with its metered usage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocks: tuple[ContentBlock, ...] = ()
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = Field(default=None, max_length=50)


class ModelOperator:
    """The host model client for the current invocation."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def complete(self, connection_ref: str, prompt: ModelPrompt) -> ModelResult:
        serialized = prompt.model_dump(mode="json")
        if len(repr(serialized).encode()) > MAX_PROMPT_BYTES:
            raise ValueError("the model prompt exceeds the declared size bound")
        response = await self._send(
            "POST",
            "/host/models/complete",
            body={"connection_ref": connection_ref, "prompt": serialized},
        )
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("the host returned a malformed model answer")
        return ModelResult.model_validate(body)

    async def embed(
        self, connection_ref: str, inputs: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        response = await self._send(
            "POST",
            "/host/models/embed",
            body={"connection_ref": connection_ref, "inputs": list(inputs)},
        )
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("the host returned a malformed embedding answer")
        vectors = body.get("vectors")
        if not isinstance(vectors, list):
            raise ValueError("the host returned a malformed embedding answer")
        rows: list[tuple[float, ...]] = []
        for vector in vectors:
            if not isinstance(vector, list):
                raise ValueError("the host returned a malformed embedding answer")
            rows.append(tuple(_embedding_component(value) for value in vector))
        return tuple(rows)

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


__all__ = [
    "MAX_PROMPT_BLOCKS",
    "MAX_PROMPT_BYTES",
    "ModelOperator",
    "ModelPrompt",
    "ModelResult",
]
