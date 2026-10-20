"""FIFO Session admission and user capacity in the caller's transaction."""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.orchestration.contracts import ExecutionBindings
from opsmesh.orchestration.runs.models import RUN_ACTIVE_STATUSES, Run
from opsmesh.temporal.dispatch.outbox import stage_signal


def is_session_head(session: Session, run: Run) -> bool:
    """The caller holds the Session lock, so only its earliest unfinished Run may execute."""
    return (
        session.scalar(
            select(Run.id)
            .where(
                Run.session_id == run.session_id,
                Run.sequence < run.sequence,
                Run.status.in_(RUN_ACTIVE_STATUSES),
            )
            .limit(1)
        )
        is None
    )


def acquire_capacity(session: Session, run: Run, bindings: ExecutionBindings) -> None:
    if run.capacity_reservation_id is not None:
        return
    run.capacity_epoch += 1
    reservation = bindings.capacity.reserve_capacity(
        session,
        user_id=run.principal_id,
        kind="concurrency",
        amount=1,
        idempotency_scope="orchestration.run",
        idempotency_key=f"{run.id}:{run.capacity_epoch}",
    )
    run.capacity_reservation_id = reservation.id


def release_capacity(session: Session, run: Run, bindings: ExecutionBindings) -> None:
    if run.capacity_reservation_id is not None:
        bindings.capacity.release_capacity(
            session,
            user_id=run.principal_id,
            reservation_id=run.capacity_reservation_id,
        )
        run.capacity_reservation_id = None
    heads = session.execute(
        select(Run.session_id, func.min(Run.sequence))
        .where(Run.principal_id == run.principal_id, Run.status == "queued")
        .group_by(Run.session_id)
    )
    for session_id, sequence in heads:
        waiting = session.scalar(
            select(Run).where(
                Run.session_id == session_id,
                Run.sequence == sequence,
                Run.status == "queued",
            )
        )
        if waiting is not None:
            stage_signal(
                session,
                dispatch_id=uuid4(),
                workflow_id=f"run:{waiting.id}",
                signal_name="wake",
                signal_args=(),
            )
