"""Administrative confirmation of existing effects; never schedules external I/O."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.contracts import ExecutionBindings, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.runs.access import load_run, lock_run_facts
from opsmesh.orchestration.runs.events import append_event
from opsmesh.orchestration.runs.models import (
    RUN_ACTIVE_STATUSES,
    OperationReceipt,
    Run,
    RunApproval,
)
from opsmesh.orchestration.runs.scheduling import release_capacity
from opsmesh.orchestration.runs.schemas import ReconcileRequest, ResumeRequest
from opsmesh.orchestration.runs.service import _cancel
from opsmesh.orchestration.runtime.models import Environment, EnvironmentOperationReceipt
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.shared.approvals import approvals_for
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.temporal.dispatch.outbox import stage_signal


def reconcile(
    session: Session,
    *,
    run_id: UUID,
    actor: CurrentUser,
    request: ReconcileRequest,
    cipher: SecretCipher,
    bindings: ExecutionBindings,
) -> Run:
    require_platform_admin(actor)
    run = lock_run_facts(session, run_id)

    def confirm() -> Run:
        if run.status != "needs_attention" or run.event_sequence != request.expected_revision:
            raise ConflictError("The Run is not at the expected reconciliation revision")
        if session.scalar(
            select(EnvironmentOperationReceipt.operation_id)
            .where(
                EnvironmentOperationReceipt.run_id == run.id,
                EnvironmentOperationReceipt.status.in_(("pending", "started", "uncertain")),
            )
            .limit(1)
        ):
            raise ConflictError(
                "Confirm runtime-owned operations through their owner before reconciling the Run"
            )
        effects = list(
            session.scalars(
                select(OperationReceipt)
                .where(
                    OperationReceipt.run_id == run.id,
                    OperationReceipt.status.in_(("started", "uncertain")),
                )
                .order_by(OperationReceipt.operation_id)
            )
        )
        unresolved = {item.operation_id for item in effects}
        resolutions = {item.operation_id: item for item in request.operations}
        if len(resolutions) != len(request.operations) or unresolved != resolutions.keys():
            raise ConflictError("Reconciliation must confirm the complete unresolved operation set")
        waiting = list(
            session.scalars(
                select(Run)
                .where(Run.session_id == run.session_id, Run.status == "queued")
                .order_by(Run.sequence)
                .with_for_update()
            )
        )
        environment_ids = sorted(
            {item.environment_id for item in effects if item.environment_id is not None}
        )
        for environment in session.scalars(
            select(Environment)
            .where(Environment.id.in_(environment_ids))
            .order_by(Environment.id)
            .with_for_update()
        ):
            if environment.active_operation_id in unresolved:
                environment.active_operation_id = None
                environment.status = "ready"
        totals = dict(run.usage)
        # Settle quantity holds before releasing active execution capacity.
        for receipt in effects:
            resolution = resolutions[receipt.operation_id]
            if resolution.outcome == "succeeded" and resolution.usage is None:
                raise ConflictError("Confirmed execution requires attested actual usage")
            if receipt.token_reservation_id is not None:
                if resolution.outcome == "not_executed":
                    bindings.tokens.release_tokens(
                        session, reservation_id=receipt.token_reservation_id
                    )
                else:
                    assert resolution.usage is not None
                    bindings.tokens.settle_tokens(
                        session,
                        reservation_id=receipt.token_reservation_id,
                        actual_amount=resolution.usage.input_tokens
                        + resolution.usage.cached_input_tokens
                        + resolution.usage.output_tokens,
                    )
        release_capacity(session, run, bindings)
        for receipt in effects:
            resolution = resolutions[receipt.operation_id]
            receipt.status = "completed"
            receipt.accounted = True
            receipt.reconciliation_ciphertext = cipher.encrypt_payload(
                {
                    "resolution": resolution.model_dump(mode="json"),
                    "evidence": request.evidence,
                    "confirmed_by": str(actor.user_id),
                    "command_id": str(request.command_id),
                }
            ).ciphertext
            receipt.result_ciphertext = cipher.encrypt_payload(
                {"result": resolution.result}
            ).ciphertext
            receipt.usage = resolution.usage.model_dump() if resolution.usage else {}
            for key, value in receipt.usage.items():
                totals[key] = totals.get(key, 0) + value
        run.usage = totals
        record_audit(
            session,
            AuditRecord(
                category="audit",
                action="run.reconciled",
                actor=AuditActor.from_user(actor),
                source="platform",
                target_type="run",
                target_id=str(run.id),
                summary="Run effects confirmed",
                payload_json=canonical_json(
                    {
                        "evidence_digest": payload_digest(request.evidence),
                        "command_id": str(request.command_id),
                        "operation_count": len(request.operations),
                    }
                ),
            ),
        )
        run.status = "failed"
        run.error_code = "reconciled_external_effects"
        run.completed_at = datetime.now(UTC)
        model_session = session.get(SessionRecord, run.session_id)
        if model_session is not None:
            model_session.status = "closing"
            for queued in waiting:
                _cancel(
                    session, run=queued, cipher=cipher, expected_revision=queued.event_sequence
                )
            other = session.scalar(
                select(Run.id)
                .where(
                    Run.session_id == run.session_id,
                    Run.id != run.id,
                    Run.status.in_(RUN_ACTIVE_STATUSES),
                )
                .limit(1)
            )
            if other is None:
                model_session.status = "closed"
        append_event(
            session,
            run=run,
            cipher=cipher,
            event_key=f"{run.id}:reconciled:{request.command_id}",
            kind="reconciled",
            payload={"status": run.status, "confirmed_by": str(actor.user_id)},
        )
        return run

    return run_idempotent_create(
        session=session,
        scope_id=run.scope_id,
        actor_id=actor.user_id,
        operation=f"execution.reconcile:{run.id}",
        idempotency_key=str(request.command_id),
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda _: run,
        create=confirm,
        resource_id=lambda item: item.id,
    )


def resume(
    session: Session,
    *,
    run_id: UUID,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    request: ResumeRequest,
) -> Run:
    run = load_run(session, run_id=run_id, subject=subject, admission=admission, lock=True)

    def signal() -> Run:
        checkpoint = session.scalar(
            select(RunApproval).where(
                RunApproval.run_id == run.id, RunApproval.approval_id == request.approval_id
            )
        )
        decision = approvals_for(session).get(session, request.approval_id)
        if (
            run.status != "waiting_approval"
            or run.event_sequence != request.expected_revision
            or checkpoint is None
            or decision is None
            or decision.status not in {"approved", "rejected"}
        ):
            raise ConflictError("No effective decision exists at the expected Run revision")
        stage_signal(
            session,
            dispatch_id=uuid4(),
            workflow_id=f"run:{run.id}",
            signal_name="approval",
            signal_args=(str(request.approval_id),),
        )
        return run

    return run_idempotent_create(
        session=session,
        scope_id=run.scope_id,
        actor_id=subject.user_id,
        operation=f"execution.resume:{run.id}",
        idempotency_key=str(request.command_id),
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda _: run,
        create=signal,
        resource_id=lambda item: item.id,
    )
