"""Host secrets: execution-boundary reads of approved secret slots.

A slot is readable only for the exact operation that declared it. Values are
returned to the execution boundary and never enter results, logs, events or
workflow history.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    Transport,
    TransportContext,
    TransportRequest,
    TransportResponse,
)

MAX_SLOT_NAME_LENGTH: Final = 100


class SecretSlotRef(BaseModel):
    """A reference to an approved secret slot of the current installation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: str = Field(min_length=1, max_length=MAX_SLOT_NAME_LENGTH)
    operation_export_id: str = Field(min_length=1, max_length=100)


class SecretReader:
    """Reads approved secret slots at the execution boundary."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def read(self, ref: SecretSlotRef) -> str:
        """Read one slot for the declaring operation.

        The host re-checks the declaration and the current authorization; a
        slot read for any other operation is rejected there.
        """
        response = await self._send(
            "POST",
            "/host/secrets/read",
            body=ref.model_dump(mode="json"),
        )
        value = response.body
        if not isinstance(value, dict):
            raise ValueError("the host returned a malformed secret payload")
        secret = value.get("value")
        if not isinstance(secret, str):
            raise ValueError("the host returned a malformed secret payload")
        return secret

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


__all__ = ["MAX_SLOT_NAME_LENGTH", "SecretReader", "SecretSlotRef"]
