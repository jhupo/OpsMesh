"""Register intents in business transactions and flush them to the native client."""

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import Table, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio.client import Client

from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.temporal.dispatch.contracts import (
    DispatchOperation,
    DispatchRequest,
    WorkflowStartOptions,
)
from opsmesh.temporal.dispatch.delivery import dispatch_workflow
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

logger = logging.getLogger(__name__)
DISPATCH_NOTIFICATION_CHANNEL = "opsmesh_temporal_dispatch"
DEFAULT_DISPATCH_CONCURRENCY = 16
DEFAULT_PUBLISH_LIMIT = 100
_CLAIM_LEASE_SECONDS = 60
_MAX_BACKOFF_SECONDS = 300
_MAX_DEFERRAL_SECONDS = 30


class DispatchOutcome(StrEnum):
    """Result of one delivery attempt for a claimed command."""

    DELIVERED = "delivered"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"


class WorkflowRequest(Protocol):
    @property
    def execution_id(self) -> UUID: ...

    @property
    def workflow_name(self) -> str: ...

    @property
    def workflow_id(self) -> str: ...

    def model_dump(self, *, mode: str) -> dict[str, Any]: ...


def stage_execution(
    session: Session,
    execution: WorkflowRequest,
    *,
    dispatch_id: UUID | None = None,
    start_options: WorkflowStartOptions | None = None,
) -> str:
    if not execution.workflow_name:
        raise ValueError("The feature must declare its workflow name")
    stage_dispatch(
        session,
        DispatchRequest(
            # The product execution is the idempotency identity for a start command.
            dispatch_id=dispatch_id or execution.execution_id,
            workflow_id=execution.workflow_id,
            operation=DispatchOperation.START,
            execution=execution.model_dump(mode="json"),
            workflow_name=execution.workflow_name,
            start_options=start_options or WorkflowStartOptions(),
        ),
    )
    return execution.workflow_id


def stage_signal(
    session: Session,
    *,
    dispatch_id: UUID,
    workflow_id: str,
    signal_name: str,
    signal_args: tuple[JsonValue, ...],
) -> None:
    stage_dispatch(
        session,
        DispatchRequest(
            dispatch_id=dispatch_id,
            workflow_id=workflow_id,
            operation=DispatchOperation.SIGNAL,
            signal_name=signal_name,
            signal_args=signal_args,
        ),
    )


def stage_cancellation(session: Session, *, dispatch_id: UUID, workflow_id: str) -> None:
    stage_dispatch(
        session,
        DispatchRequest(
            dispatch_id=dispatch_id,
            workflow_id=workflow_id,
            operation=DispatchOperation.CANCEL,
        ),
    )


def stage_dispatch(session: Session, request: DispatchRequest) -> None:
    cipher = session.info.get("dispatch_cipher")
    if not isinstance(cipher, SecretCipher):
        raise RuntimeError("Dispatch encryption must be supplied by the session owner")
    capture_context = session.info.get("dispatch_trace_context")
    # Match ORM execute autoflush. Mapper listeners can stage on this same connection.
    session.flush()
    persist_dispatch(
        session.connection(),
        request,
        cipher=cipher,
        trace_context=capture_context() if callable(capture_context) else None,
    )


def persist_dispatch(
    connection: Connection,
    request: DispatchRequest,
    *,
    cipher: SecretCipher,
    trace_context: Mapping[str, str] | None = None,
) -> None:
    """Stage a sealed command on the caller's transaction, including during ORM flush."""
    if trace_context is not None and request.traceparent is None:
        request = DispatchRequest.model_validate(
            request.model_dump(mode="json")
            | {
                "traceparent": trace_context.get("traceparent"),
                "tracestate": trace_context.get("tracestate"),
            }
        )
    sealed = cipher.encrypt_payload(request.model_dump(mode="json"))
    table = cast(Table, TemporalOutboxEntry.__table__)
    inserted = connection.scalar(
        insert(table)
        .values(
            id=request.dispatch_id,
            workflow_id=request.workflow_id,
            operation=request.operation.value,
            sealed_payload=sealed.ciphertext,
            encryption_key_id=sealed.key_id,
            available_at=datetime.now(UTC),
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[table.c.id])
        .returning(table.c.id)
    )
    if inserted is None:
        existing = (
            connection.execute(select(table).where(table.c.id == request.dispatch_id))
            .mappings()
            .one_or_none()
        )
        # Delivery identity belongs to the business command, not its retry's trace.
        if existing is None or DispatchRequest.model_validate(
            cipher.decrypt_payload(existing["sealed_payload"], key_id=existing["encryption_key_id"])
        ).model_dump(mode="json", exclude={"traceparent", "tracestate"}) != request.model_dump(
            mode="json", exclude={"traceparent", "tracestate"}
        ):
            raise ValueError("Dispatch identity is already bound to a different operation")
    # PostgreSQL releases NOTIFY only after the encompassing business transaction commits.
    connection.execute(select(func.pg_notify(DISPATCH_NOTIFICATION_CHANNEL, "")))


class TemporalOutboxPublisher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: Client,
        *,
        task_queue: str,
        concurrency: int = DEFAULT_DISPATCH_CONCURRENCY,
        cipher: SecretCipher,
        restore_context: Callable[[Mapping[str, str]], AbstractContextManager[None]] | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Temporal dispatch concurrency must be positive")
        self._cipher = cipher
        self._restore_context = restore_context
        self._sessions = sessions
        self._client = client
        self._task_queue = task_queue
        self._concurrency = concurrency

    async def publish_pending(self, *, limit: int = DEFAULT_PUBLISH_LIMIT) -> int:
        delivered = 0
        remaining = max(0, limit)
        while remaining:
            requests = await self._claim(min(remaining, self._concurrency))
            if not requests:
                break
            remaining -= len(requests)
            # Network delivery never holds a PostgreSQL connection. The bound is
            # per publisher; other nodes claim independently through SKIP LOCKED.
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(self._deliver(*item)) for item in requests]
            delivered += sum(task.result() for task in tasks)
        return delivered

    async def _claim(self, limit: int) -> list[tuple[UUID, UUID, DispatchRequest]]:
        claimed: list[tuple[UUID, UUID, DispatchRequest]] = []
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(TemporalOutboxEntry)
                    .where(
                        TemporalOutboxEntry.published_at.is_(None),
                        TemporalOutboxEntry.available_at <= now,
                        (TemporalOutboxEntry.claim_until.is_(None))
                        | (TemporalOutboxEntry.claim_until < now),
                    )
                    .order_by(TemporalOutboxEntry.available_at, TemporalOutboxEntry.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                try:
                    request = DispatchRequest.model_validate(
                        self._cipher.decrypt_payload(
                            row.sealed_payload, key_id=row.encryption_key_id
                        )
                    )
                    if (
                        request.dispatch_id != row.id
                        or request.workflow_id != row.workflow_id
                        or request.operation.value != row.operation
                    ):
                        raise ValueError("Dispatch reference differs from its durable record")
                except Exception as exc:
                    row.attempts += 1
                    row.available_at = now + timedelta(
                        seconds=min(2 ** min(row.attempts, 8), _MAX_BACKOFF_SECONDS)
                    )
                    row.claim_token = None
                    row.claim_until = None
                    row.last_error_code = type(exc).__name__[:100]
                    continue
                token = uuid4()
                row.claim_token = token
                row.claim_until = now + timedelta(seconds=_CLAIM_LEASE_SECONDS)
                claimed.append((row.id, token, request))
        return claimed

    async def _deliver(self, dispatch_id: UUID, token: UUID, request: DispatchRequest) -> int:
        carrier = {
            key: value
            for key, value in (
                ("traceparent", request.traceparent),
                ("tracestate", request.tracestate),
            )
            if value is not None
        }
        context = self._restore_context(carrier) if self._restore_context else nullcontext()
        try:
            with context:
                outcome = await self._deliver_command(dispatch_id, token, request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._record_failure(dispatch_id, token, type(exc).__name__[:100])
            return 0
        if outcome is DispatchOutcome.DEFERRED:
            await self._record_deferral(dispatch_id, token)
            return 0
        if outcome is DispatchOutcome.SUPERSEDED or outcome is DispatchOutcome.DELIVERED:
            await self._record_published(dispatch_id, token)
            return 1
        # The claim expired before the ordering decision; the current owner delivers it.
        return 0

    async def _deliver_command(
        self, dispatch_id: UUID, token: UUID, request: DispatchRequest
    ) -> DispatchOutcome | None:
        """Deliver one command, keeping start and cancel causally ordered.

        Both checks and the cancel intent run in one transaction that holds a
        per-workflow advisory lock, so concurrent commands for one Workflow are
        serialized. ``decided_at`` persists a cancel intent before its delivery
        is attempted and ``published_at`` is only ever set after a successful
        delivery, never before. A command therefore stays claimable until its
        delivery is confirmed, so an interrupted delivery (process exit, task
        cancellation) is retried instead of being lost. A start is superseded as
        soon as a cancel intent exists, decided or confirmed, so a cancellation
        can never complete while the Workflow is still started afterwards; a
        cancel is deferred while its start is not yet confirmed, including while
        the start delivery is in flight. Signals carry no ordering contract and
        are delivered directly.
        """
        if request.operation == DispatchOperation.SIGNAL:
            await dispatch_workflow(self._client, request, task_queue=self._task_queue)
            return DispatchOutcome.DELIVERED
        async with self._sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:workflow_id, 0))"),
                {"workflow_id": request.workflow_id},
            )
            row = await session.scalar(
                select(TemporalOutboxEntry)
                .where(
                    TemporalOutboxEntry.id == dispatch_id,
                    TemporalOutboxEntry.claim_token == token,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if request.operation == DispatchOperation.START:
                cancelled = await self._has_sibling(
                    session, request.workflow_id, DispatchOperation.CANCEL, decided=True
                )
                if cancelled:
                    logger.info(
                        "Dispatch start superseded by an earlier cancellation",
                        extra={"dispatch_id": str(request.dispatch_id)},
                    )
                    # Superseded is a terminal decision: nothing will be delivered.
                    row.published_at = datetime.now(UTC)
                    return DispatchOutcome.SUPERSEDED
            elif request.operation == DispatchOperation.CANCEL:
                starting = await self._has_sibling(
                    session, request.workflow_id, DispatchOperation.START, confirmed=False
                )
                if starting:
                    return DispatchOutcome.DEFERRED
                # The cancellation intent is durable before the delivery is
                # attempted, so a start staged in the meantime is superseded
                # even if this cancel has not completed yet.
                row.decided_at = datetime.now(UTC)
            # The decision only authorizes the delivery; the command stays
            # unconfirmed until _record_published observes the Temporal result.
        await dispatch_workflow(self._client, request, task_queue=self._task_queue)
        return DispatchOutcome.DELIVERED

    async def _has_sibling(
        self,
        session: AsyncSession,
        workflow_id: str,
        operation: DispatchOperation,
        *,
        decided: bool | None = None,
        confirmed: bool | None = None,
    ) -> bool:
        """Whether a sibling command exists with the requested decision state.

        ``decided`` filters on a persisted decision (cancel intent);
        ``confirmed`` filters on a completed delivery (``published_at``).
        """
        conditions = [
            TemporalOutboxEntry.workflow_id == workflow_id,
            TemporalOutboxEntry.operation == operation.value,
        ]
        if decided is not None:
            conditions.append(
                TemporalOutboxEntry.decided_at.is_not(None)
                if decided
                else TemporalOutboxEntry.decided_at.is_(None)
            )
        if confirmed is not None:
            conditions.append(
                TemporalOutboxEntry.published_at.is_not(None)
                if confirmed
                else TemporalOutboxEntry.published_at.is_(None)
            )
        count = await session.scalar(
            select(func.count()).select_from(TemporalOutboxEntry).where(*conditions)
        )
        return bool(count)

    async def _record_published(self, dispatch_id: UUID, token: UUID) -> None:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(TemporalOutboxEntry)
                .where(
                    TemporalOutboxEntry.id == dispatch_id, TemporalOutboxEntry.claim_token == token
                )
                .with_for_update()
            )
            if row is not None:
                row.published_at = datetime.now(UTC)
                row.claim_token = None
                row.claim_until = None
                row.last_error_code = None

    async def _record_failure(self, dispatch_id: UUID, token: UUID, error_code: str) -> None:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(TemporalOutboxEntry)
                .where(
                    TemporalOutboxEntry.id == dispatch_id, TemporalOutboxEntry.claim_token == token
                )
                .with_for_update()
            )
            if row is None:
                return
            # Release the cancel intent so a failed cancellation stops blocking
            # the start it was meant to stop; the retry re-decides the ordering.
            row.decided_at = None
            row.attempts += 1
            row.available_at = datetime.now(UTC) + timedelta(
                seconds=min(2 ** min(row.attempts, 8), _MAX_BACKOFF_SECONDS)
            )
            row.claim_token = None
            row.claim_until = None
            row.last_error_code = error_code
            logger.error(
                "Temporal dispatch failed",
                extra={
                    "dispatch_id": str(row.id),
                    "error_code": error_code,
                },
            )

    async def _record_deferral(self, dispatch_id: UUID, token: UUID) -> None:
        """Reschedule a command whose sibling command must be delivered first."""
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(TemporalOutboxEntry)
                .where(
                    TemporalOutboxEntry.id == dispatch_id, TemporalOutboxEntry.claim_token == token
                )
                .with_for_update()
            )
            if row is None:
                return
            row.attempts += 1
            row.available_at = now + timedelta(
                seconds=min(2 ** min(row.attempts, 5), _MAX_DEFERRAL_SECONDS)
            )
            row.claim_token = None
            row.claim_until = None
            row.last_error_code = "awaiting_sibling_command"
        logger.info(
            "Dispatch deferred until its sibling command is delivered",
            extra={"dispatch_id": str(dispatch_id)},
        )

    async def next_delivery_at(self) -> datetime | None:
        async with self._sessions() as session:
            return cast(
                datetime | None,
                await session.scalar(
                    select(
                        func.min(
                            func.greatest(
                                TemporalOutboxEntry.available_at,
                                func.coalesce(
                                    TemporalOutboxEntry.claim_until,
                                    TemporalOutboxEntry.available_at,
                                ),
                            )
                        )
                    ).where(TemporalOutboxEntry.published_at.is_(None))
                ),
            )
