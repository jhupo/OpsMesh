"""Normalize confirmed quantities; retain unknown operations and their Token holds."""

from agents.usage import Usage
from sqlalchemy.orm import Session

from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.orchestration.contracts import ExecutionBindings
from opsmesh.orchestration.execution.model_provider import model_usage
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import ConflictError


def audit_actor(run: Run) -> AuditActor:
    return AuditActor(
        actor_type="user",
        actor_id=str(run.initiated_by_id or run.principal_id),
        configuration_id=None if run.owner_ref.startswith("user:") else run.scope_id,
    )


def normalize_usage(usage: Usage) -> NormalizedUsage:
    return NormalizedUsage.model_validate(model_usage(usage))


def settle_usage(
    session: Session,
    *,
    run: Run,
    receipt: OperationReceipt,
    usage: NormalizedUsage,
    bindings: ExecutionBindings,
) -> None:
    if receipt.accounted:
        if receipt.usage != usage.model_dump():
            raise ConflictError("Confirmed operation usage cannot change")
        return
    if receipt.token_reservation_id is not None:
        bindings.tokens.settle_tokens(
            session,
            reservation_id=receipt.token_reservation_id,
            actual_amount=usage.input_tokens + usage.cached_input_tokens + usage.output_tokens,
        )
    receipt.usage = usage.model_dump()
    receipt.accounted = True
    totals = dict(run.usage)
    for key, value in receipt.usage.items():
        totals[key] = totals.get(key, 0) + value
    run.usage = totals


def retain_uncertain(session: Session, *, run: Run, receipt: OperationReceipt) -> None:
    if receipt.accounted:
        return
    receipt.status = "uncertain"
