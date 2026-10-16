"""Host jobs: submit fixed flows and steps, returning a FlowRunRef.

Jobs delegate to the single orchestration engine; there is no independent job
runner. Every submission carries the current scope, the parent chain and an
idempotency key, and returns a flow run reference for progress and cancel.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    Transport,
    TransportContext,
    TransportRequest,
    TransportResponse,
)
from opsmesh_sdk.values import FlowRunRef, IdempotencyKey

MAX_JOB_INPUT_BYTES: Final = 262_144


class JobSubmission(BaseModel):
    """A fixed flow or step submission from the current invocation scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    definition_revision: str = Field(min_length=1, max_length=200)
    entry_step: str = Field(min_length=1, max_length=100)
    input: dict[str, JsonValue] = Field(default_factory=dict)
    idempotency_key: IdempotencyKey
    parent_ref: str | None = Field(default=None, max_length=128)


class JobSubmitter:
    """The host jobs client for the current invocation."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    async def submit(self, submission: JobSubmission) -> FlowRunRef:
        serialized = submission.model_dump(mode="json")
        if len(repr(serialized).encode()) > MAX_JOB_INPUT_BYTES:
            raise ValueError("the job input exceeds the declared size bound")
        response = await self._send(
            "POST",
            "/host/jobs",
            body=serialized,
            idempotency_key=submission.idempotency_key,
        )
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("the host returned a malformed job receipt")
        return FlowRunRef.model_validate(body)

    async def progress(self, flow_run_id: str) -> dict[str, JsonValue]:
        response = await self._send("GET", f"/host/jobs/{flow_run_id}/progress")
        body = response.body
        return body if isinstance(body, dict) else {}

    async def cancel(self, flow_run_id: str, *, idempotency_key: str) -> None:
        await self._send(
            "POST",
            f"/host/jobs/{flow_run_id}/cancel",
            body={},
            idempotency_key=idempotency_key,
        )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        body: object = None,
        idempotency_key: str | None = None,
    ) -> TransportResponse:
        request = TransportRequest(
            method=method,
            path=path,
            body=body,
            idempotency_key=idempotency_key,
            stream=False,
        )
        return await self._transport.send(request, TransportContext(scope=CancellationScope()))


__all__ = ["MAX_JOB_INPUT_BYTES", "JobSubmission", "JobSubmitter"]
