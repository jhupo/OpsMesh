"""Approval decisions are tied to one persisted native interruption."""

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.execution.runs.access import get_run
from opsmesh.execution.runs.events import append_event
from opsmesh.execution.runs.models import ExecutionRun
from opsmesh.execution.runs.schemas import ApprovalDecision
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.access import CurrentUser


def decide_approval(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    request: ApprovalDecision,
    cipher: SecretCipher,
) -> ExecutionRun:
    row = get_run(session, actor=actor, run_id=run_id, lock=True)
    if row.approval_id != request.approval_id:
        raise ConflictError("The approval no longer refers to the current interruption")
    if row.approval_decision is not None:
        if row.approval_decision != request.allow:
            raise ConflictError("The approval already has a different decision")
        return row
    if row.status != "interrupted" or not row.encrypted_state:
        raise ConflictError("The run is not waiting for approval")
    row.approval_decision, row.status = request.allow, "queued"
    append_event(
        session,
        run_id=row.id,
        kind="approval_decided",
        payload={"approval_id": str(request.approval_id), "allow": request.allow},
        cipher=cipher,
    )
    return row
