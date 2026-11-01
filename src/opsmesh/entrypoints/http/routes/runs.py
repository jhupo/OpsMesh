"""Published-agent runs, approvals and resumable server-sent events."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator

from opsmesh.framework.agents import PendingToolApproval
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.runtime.api import (
    AgentRunView,
    RunApprovalPolicy,
    RunOverrides,
    RunStatus,
    RunStreamEvent,
)

from ..security import CurrentUser, get_application
from ..worker_diagnostics import RunWorkerView, WorkerDiagnosticsView, public_diagnostics

router = APIRouter(prefix="/runs", tags=["runs"])
logger = logging.getLogger(__name__)


class SubmitRunBody(OpsMeshContract):
    session_id: str = Field(min_length=1)
    agent_ref: str = Field(min_length=1)
    input: str | list[dict[str, Any]]
    idempotency_key: str = Field(min_length=1, max_length=200)
    overrides: RunOverrides = Field(default_factory=RunOverrides)

    @field_validator("input")
    @classmethod
    def user_messages_only(cls, value: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        if isinstance(value, list):
            for item in value:
                if (
                    item.get("role") != "user"
                    or item.get("type", "message") != "message"
                    or set(item) - {"role", "type", "content"}
                    or "content" not in item
                ):
                    raise ValueError("HTTP input accepts user messages only")
        return value


class ResumeRunBody(OpsMeshContract):
    expected_revision: int = Field(ge=1)


class ApprovalDecisionBody(ResumeRunBody):
    approved: bool
    reason: str = Field(default="", max_length=2000)


class ApprovalPolicyBody(ResumeRunBody):
    policy: RunApprovalPolicy


@router.post("", status_code=202)
async def submit(body: SubmitRunBody, request: Request, user: CurrentUser) -> AgentRunView:
    return await get_application(request).services.run_management.submit(
        actor_id=user.id,
        session_id=body.session_id,
        agent_ref=body.agent_ref,
        input=body.input,
        idempotency_key=body.idempotency_key,
        overrides=body.overrides,
    )


@router.get("")
async def runs(
    request: Request,
    user: CurrentUser,
    principal_id: str | None = None,
    session_id: str | None = None,
    status: RunStatus | None = None,
) -> list[AgentRunView]:
    return await get_application(request).services.run_management.list_runs(
        actor_id=user.id, principal_id=principal_id, session_id=session_id, status=status
    )


async def authorized_run(
    request: Request, user: CurrentUser, run_id: str, action: str
) -> AgentRunView:
    manager = get_application(request).services.run_management
    run = await manager.get_run(run_id, actor_id=user.id)
    await manager.authorize(user.id, run, action)
    return run


@router.get("/{run_id}")
async def run(run_id: str, request: Request, user: CurrentUser) -> AgentRunView:
    return await authorized_run(request, user, run_id, "run:read")


@router.get("/{run_id}/worker")
async def worker(
    run_id: str, request: Request, response: Response, user: CurrentUser
) -> RunWorkerView | None:
    await authorized_run(request, user, run_id, "run:read")
    response.headers["Cache-Control"] = "no-store"
    process = await get_application(request).launcher.poll(run_id)
    return RunWorkerView.from_process(process) if process is not None else None


@router.get("/{run_id}/worker/logs")
async def worker_logs(
    run_id: str,
    request: Request,
    response: Response,
    user: CurrentUser,
    limit_bytes: Annotated[int, Query(ge=1024, le=1_000_000)] = 64_000,
) -> WorkerDiagnosticsView:
    await authorized_run(request, user, run_id, "run:read")
    response.headers["Cache-Control"] = "no-store"
    raw = await get_application(request).launcher.logs(run_id)
    return public_diagnostics(raw, run_id, limit_bytes)


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, request: Request, user: CurrentUser) -> AgentRunView:
    return await get_application(request).services.run_management.cancel(run_id, actor_id=user.id)


@router.get("/{run_id}/approvals")
async def approvals(
    run_id: str, request: Request, user: CurrentUser
) -> tuple[PendingToolApproval, ...]:
    return await get_application(request).services.run_management.list_approvals(
        run_id, actor_id=user.id
    )


@router.post("/{run_id}/approvals/{approval_id}")
async def decide(
    run_id: str, approval_id: str, body: ApprovalDecisionBody, request: Request, user: CurrentUser
) -> AgentRunView:
    return await get_application(request).services.run_management.decide(
        run_id,
        approval_id,
        body.approved,
        body.expected_revision,
        actor_id=user.id,
        reason=body.reason,
    )


@router.post("/{run_id}/resume", status_code=202)
async def resume(
    run_id: str, body: ResumeRunBody, request: Request, user: CurrentUser
) -> AgentRunView:
    return await get_application(request).services.run_management.resume(
        run_id,
        body.expected_revision,
        actor_id=user.id,
    )


@router.get("/{run_id}/events")
async def events(
    run_id: str, request: Request, user: CurrentUser, after_seq: Annotated[int, Query(ge=0)] = 0
) -> StreamingResponse:
    await authorized_run(request, user, run_id, "run:read")
    last_id = request.headers.get("Last-Event-ID")
    if last_id is not None:
        if not last_id.isdecimal() or len(last_id) > 20:
            raise OpsMeshError(
                "INVALID_REQUEST", "Last-Event-ID must be a nonnegative sequence number"
            )
        after_seq = max(after_seq, int(last_id))
    app = get_application(request)

    async def stream() -> AsyncIterator[str]:
        iterator = app.services.run_management.subscribe(
            run_id, after_seq, actor_id=user.id
        ).__aiter__()

        async def next_event() -> RunStreamEvent:
            return await anext(iterator)

        pending = asyncio.create_task(next_event())
        last_authorization = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=app.settings.sse_heartbeat_seconds)
                disconnected = await request.is_disconnected()
                # Starlette polls receive inside a cancelled AnyIO scope. If an
                # external task cancellation races with that probe, the scope may
                # consume its exception while leaving the cancellation requested.
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise asyncio.CancelledError
                if disconnected:
                    break
                if time.monotonic() - last_authorization >= app.settings.sse_heartbeat_seconds:
                    current = await app.services.accounts.authenticate(request.state.bearer_token)
                    await authorized_run(request, current, run_id, "run:read")
                    last_authorization = time.monotonic()
                if not done:
                    yield ": heartbeat\n\n"
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                kind = (
                    event.kind if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", event.kind) else "event"
                )
                yield f"id: {event.seq}\nevent: {kind}\ndata: {event.model_dump_json()}\n\n"
                pending = asyncio.create_task(next_event())
        except OpsMeshError as exc:
            yield "event: error\ndata: " + json.dumps({"code": exc.code}) + "\n\n"
        except Exception:
            run_id_fingerprint = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
            logger.exception("Run stream failed for %s", run_id_fingerprint)
            yield 'event: error\ndata: {"code":"INTERNAL_ERROR"}\n\n'
        finally:

            async def close_subscription() -> None:
                pending.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await pending
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()

            cleanup = asyncio.create_task(close_subscription())
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/recover", status_code=202)
async def recover(
    run_id: str, body: ResumeRunBody, request: Request, user: CurrentUser
) -> AgentRunView:
    return await get_application(request).services.run_management.recover(
        run_id, body.expected_revision, actor_id=user.id
    )


@router.put("/{run_id}/approval-policy")
async def approval_policy(
    run_id: str, body: ApprovalPolicyBody, request: Request, user: CurrentUser
) -> AgentRunView:
    return await get_application(request).services.run_management.set_approval_policy(
        run_id, body.policy, body.expected_revision, actor_id=user.id
    )
