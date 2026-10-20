"""Same-transaction per-operation Token bounds shared by a flow and its descendants."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.orchestration.definitions.contracts import FlowLimits
from opsmesh.orchestration.runs.models import FlowCommand, FlowRun, FlowTokenGrant
from opsmesh.shared.errors import ConflictError, InvalidArgumentError


def reserve_root_bound(
    session: Session,
    *,
    root_id: UUID,
    operation_id: str,
    tokens: int,
) -> None:
    if tokens < 0:
        raise InvalidArgumentError("Root Token admission bounds must be nonnegative")
    session.flush()
    # Claims already hold quota locks. NOWAIT avoids a reverse lock wait
    # against a controller that holds the root while cancelling child owners.
    root = session.scalar(
        select(FlowRun)
        .where(FlowRun.id == root_id)
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    )
    if (
        root is None
        or root.status not in {"queued", "running", "waiting"}
        or datetime.now(UTC) >= root.deadline
    ):
        raise ConflictError("The root flow no longer admits operations")
    command = session.scalar(
        select(FlowCommand.kind)
        .where(
            FlowCommand.run_id == root.id,
            FlowCommand.kind.in_(("pause", "resume", "retry", "cancel")),
        )
        .order_by(FlowCommand.sequence.desc())
        .limit(1)
    )
    if command in {"pause", "cancel"}:
        raise ConflictError("The root flow is paused or cancelling")
    previous = session.scalar(
        select(FlowTokenGrant).where(
            FlowTokenGrant.root_id == root.id, FlowTokenGrant.operation_id == operation_id
        )
    )
    if previous:
        if previous.tokens != tokens:
            raise ConflictError("The root admission occurrence changed")
        return
    limits = FlowLimits.model_validate(root.limits)
    if root.allocated_tokens + tokens > limits.model_tokens:
        raise ConflictError("The shared root Token bound is exhausted")
    root.allocated_tokens += tokens
    session.add(FlowTokenGrant(root_id=root.id, operation_id=operation_id, tokens=tokens))
    session.flush()
