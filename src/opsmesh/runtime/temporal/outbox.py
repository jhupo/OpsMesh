"""PostgreSQL commits business changes and their Temporal delivery commands together."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from opentelemetry import propagate
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import (
    DispatchOperation,
    DispatchRequest,
    ExecutionRequest,
    WorkflowSignal,
)
from opsmesh.runtime.temporal.models import TemporalOutboxEntry

logger = logging.getLogger(__name__)
DISPATCH_NOTIFICATION_CHANNEL = "opsmesh_temporal_dispatch"


def stage_execution(
    session: Session, execution: ExecutionRequest, *, dispatch_id: UUID | None = None
) -> str:
    stage_dispatch(
        session,
        DispatchRequest(
            # The product execution is the idempotency identity for a start command.
            dispatch_id=dispatch_id or execution.execution_id,
            workspace_id=execution.workspace_id,
            workflow_id=execution.workflow_id,
            operation=DispatchOperation.START,
            execution=execution,
        ),
    )
    return execution.workflow_id


def stage_signal(
    session: Session,
    *,
    dispatch_id: UUID,
    workspace_id: UUID,
    workflow_id: str,
    signal_name: str,
    resource_id: UUID,
) -> None:
    stage_dispatch(
        session,
        DispatchRequest(
            dispatch_id=dispatch_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            operation=DispatchOperation.SIGNAL,
            signal_name=signal_name,
            signal=WorkflowSignal(resource_id=resource_id),
        ),
    )


def stage_cancellation(
    session: Session, *, dispatch_id: UUID, workspace_id: UUID, workflow_id: str
) -> None:
    stage_dispatch(
        session,
        DispatchRequest(
            dispatch_id=dispatch_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            operation=DispatchOperation.CANCEL,
        ),
    )


def stage_dispatch(session: Session, request: DispatchRequest) -> None:
    if request.traceparent is None:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        request = request.model_copy(
            update={
                "traceparent": carrier.get("traceparent"),
                "tracestate": carrier.get("tracestate"),
            }
        )
    inserted = session.scalar(
        insert(TemporalOutboxEntry)
        .values(
            id=request.dispatch_id,
            workspace_id=request.workspace_id,
            workflow_id=request.workflow_id,
            operation=request.operation.value,
            payload=request.model_dump(mode="json"),
            available_at=datetime.now(UTC),
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[TemporalOutboxEntry.id])
        .returning(TemporalOutboxEntry.id)
    )
    if inserted is None:
        existing = session.scalar(
            select(TemporalOutboxEntry).where(TemporalOutboxEntry.id == request.dispatch_id)
        )
        # Delivery identity belongs to the business command, not its retry's trace.
        if existing is None or DispatchRequest.model_validate(existing.payload).model_dump(
            mode="json", exclude={"traceparent", "tracestate"}
        ) != request.model_dump(mode="json", exclude={"traceparent", "tracestate"}):
            raise ValueError("Dispatch identity is already bound to a different operation")
    # PostgreSQL releases NOTIFY only after the encompassing business transaction commits.
    session.execute(select(func.pg_notify(DISPATCH_NOTIFICATION_CHANNEL, "")))


class TemporalOutboxPublisher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: TemporalExecutionClient,
        *,
        concurrency: int = 16,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Temporal dispatch concurrency must be positive")
        self._sessions = sessions
        self._client = client
        self._concurrency = concurrency

    async def publish_pending(self, *, limit: int = 100) -> int:
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
                    request = DispatchRequest.model_validate(row.payload)
                    if (
                        request.workspace_id != row.workspace_id
                        or request.dispatch_id != row.id
                        or request.workflow_id != row.workflow_id
                        or request.operation.value != row.operation
                    ):
                        raise ValueError("Dispatch reference differs from its durable record")
                except Exception as exc:
                    row.attempts += 1
                    row.available_at = now + timedelta(seconds=min(2 ** min(row.attempts, 8), 300))
                    row.claim_token = None
                    row.claim_until = None
                    row.last_error_code = type(exc).__name__[:100]
                    continue
                token = uuid4()
                row.claim_token = token
                row.claim_until = now + timedelta(seconds=60)
                claimed.append((row.id, token, request))
        return claimed

    async def _deliver(self, dispatch_id: UUID, token: UUID, request: DispatchRequest) -> int:
        try:
            await self._client.dispatch(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._record_failure(dispatch_id, token, type(exc).__name__[:100])
            return 0
        await self._record_published(dispatch_id, token)
        return 1

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
            row.attempts += 1
            row.available_at = datetime.now(UTC) + timedelta(
                seconds=min(2 ** min(row.attempts, 8), 300)
            )
            row.claim_token = None
            row.claim_until = None
            row.last_error_code = error_code
            logger.error(
                "Temporal dispatch failed",
                extra={
                    "workspace_id": str(row.workspace_id),
                    "dispatch_id": str(row.id),
                    "error_code": error_code,
                },
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
