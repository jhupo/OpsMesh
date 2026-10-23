"""Explicit administrative resolution of runs with unconfirmed external effects."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import current_user
from opsmesh.execution.runs.events import append_event
from opsmesh.execution.runs.models import ExecutionOperation, ExecutionRun
from opsmesh.execution.runs.service import release_run
from opsmesh.platform.billing import service as billing
from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.access import CurrentUser
from opsmesh.users.quotas import service as quotas


class EffectResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=500)
    outcome: Literal["executed", "not_executed"]
    actual_tokens: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    evidence: str = Field(min_length=1, max_length=4000)


def reconcile_effect(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    request: EffectResolution,
    cipher: SecretCipher,
) -> ExecutionRun:
    actor = current_user(session, actor.user_id)
    if not actor.has_platform_role("admin"):
        raise ForbiddenError("Only an administrator may reconcile external effects")
    row = session.get(ExecutionRun, run_id, with_for_update=True)
    if row is None:
        raise NotFoundError("The run does not exist")
    receipt = session.get(ExecutionOperation, request.operation_id, with_for_update=True)
    if receipt is None or receipt.run_id != run_id:
        raise NotFoundError("The operation does not belong to this run")
    resolution = {**request.model_dump(mode="json"), "actor_id": str(actor.user_id)}
    if receipt.reconciliation is not None:
        if receipt.reconciliation != resolution:
            raise ConflictError("The operation already has a different reconciliation")
        return row
    if row.status != "needs_attention":
        raise ConflictError("Only a stopped run requiring attention may be reconciled")
    if receipt.status not in {"started", "uncertain"}:
        raise ConflictError("The operation already has a confirmed outcome")
    if receipt.reservation_id is not None:
        if request.outcome == "not_executed":
            if any(
                value not in {None, 0}
                for value in (request.actual_tokens, request.input_tokens, request.output_tokens)
            ):
                raise ConflictError("An unexecuted model operation cannot have token usage")
            billing.settle(session, receipt.id, input_tokens=0, output_tokens=0, released=True)
            quotas.release_tokens(session, reservation_id=receipt.reservation_id)
        else:
            if (
                request.input_tokens is None
                or request.output_tokens is None
                or request.actual_tokens != request.input_tokens + request.output_tokens
            ):
                raise ConflictError(
                    "Cost reconciliation requires confirmed input and output token counts"
                )
            billing.settle(
                session,
                receipt.id,
                input_tokens=request.input_tokens,
                output_tokens=request.output_tokens,
            )
            quotas.settle_tokens(
                session,
                reservation_id=receipt.reservation_id,
                actual_amount=request.input_tokens + request.output_tokens,
            )
    elif any(
        value is not None
        for value in (request.actual_tokens, request.input_tokens, request.output_tokens)
    ):
        raise ConflictError("Only model operations accept token usage")
    receipt.status, receipt.reconciliation = "reconciled", resolution
    session.flush()
    remaining = session.scalar(
        select(ExecutionOperation.id)
        .where(
            ExecutionOperation.run_id == run_id,
            ExecutionOperation.status.in_(("started", "uncertain")),
        )
        .limit(1)
    )
    if remaining is None:
        row.status, row.error_code = "failed", "effects_reconciled"
        release_run(session, row, release_environment=True)
    append_event(
        session, run_id=run_id, kind="effect_reconciled", payload=resolution, cipher=cipher
    )
    return row
