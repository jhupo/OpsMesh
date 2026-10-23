"""Durable workflow tool effects and explicit resolution of unknown results."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.shared.errors import ConflictError, ForbiddenError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.users.service import current_user
from opsmesh.workflows.runs.access import authorize_run
from opsmesh.workflows.runs.events import append_event
from opsmesh.workflows.runs.models import WorkflowNodeRun, WorkflowOperation, WorkflowRun
from opsmesh.workflows.runs.schemas import EffectResolution
from opsmesh.workflows.schema import validate_json


def settle_run(
    session: Session, *, actor: CurrentUser, run_id: UUID, cipher: SecretCipher
) -> WorkflowRun:
    actor = current_user(session, actor.user_id)
    if not actor.has_platform_role("admin"):
        raise ForbiddenError("Only an administrator may settle workflow effects")
    row = session.get(WorkflowRun, run_id, with_for_update=True)
    if row is None:
        raise NotFoundError("The workflow run does not exist")
    if row.status == "failed" and row.error_code == "effects_reconciled":
        return row
    if row.status != "needs_attention":
        raise ConflictError("The workflow is not awaiting reconciliation")
    from opsmesh.execution.runs.models import ExecutionRun

    unknown = session.scalar(
        select(
            exists().where(
                WorkflowOperation.run_id == run_id,
                WorkflowOperation.status.in_(("started", "uncertain")),
            )
        )
    )
    linked = session.scalar(
        select(
            exists().where(
                WorkflowNodeRun.run_id == run_id,
                WorkflowNodeRun.execution_run_id == ExecutionRun.id,
                ExecutionRun.status.in_(
                    ("queued", "running", "cancelling", "interrupted", "needs_attention")
                ),
            )
        )
    )
    if unknown or linked:
        raise ConflictError("Reconcile the workflow and linked Agent effects first")
    row.status, row.error_code = "failed", "effects_reconciled"
    append_event(
        session,
        run_id=run_id,
        kind="effects_settled",
        payload={"actor_id": str(actor.user_id)},
        cipher=cipher,
    )
    return row


@dataclass(frozen=True)
class OperationLedger:
    sessions: sessionmaker[Session]
    cipher: SecretCipher
    run_id: UUID

    def claim(
        self, operation_id: str, node_id: UUID, payload: dict[str, object]
    ) -> dict[str, object] | None:
        validate_json(payload)
        fingerprint = payload_digest(payload)
        with self.sessions.begin() as session:
            authorize_run(session, self.run_id, self.cipher, lock=True)
            node = session.get(WorkflowNodeRun, node_id, with_for_update=True)
            if node is None or node.run_id != self.run_id or node.status != "running":
                raise ConflictError("The tool node is not active")
            previous = session.get(WorkflowOperation, operation_id, with_for_update=True)
            if previous is not None:
                if (
                    previous.run_id != self.run_id
                    or previous.node_id != node_id
                    or previous.fingerprint != fingerprint
                ):
                    raise ConflictError("The effect identity was reused")
                if previous.status == "completed" and previous.encrypted_result:
                    return self.cipher.decrypt_payload(previous.encrypted_result)
                raise ConflictError("The external effect requires reconciliation")
            session.add(
                WorkflowOperation(
                    id=operation_id,
                    run_id=self.run_id,
                    node_id=node_id,
                    fingerprint=fingerprint,
                    status="started",
                )
            )
        return None

    def complete(self, operation_id: str, value: object) -> None:
        payload = {"output": value}
        validate_json(payload, max_bytes=4_000_000)
        encrypted = self.cipher.encrypt_payload(payload)
        with self.sessions.begin() as session:
            receipt = session.get(WorkflowOperation, operation_id, with_for_update=True)
            if (
                receipt is None
                or receipt.run_id != self.run_id
                or receipt.status not in {"started", "completed"}
            ):
                raise ConflictError("The effect receipt is no longer owned")
            if receipt.status == "completed":
                if (
                    receipt.encrypted_result is None
                    or self.cipher.decrypt_payload(receipt.encrypted_result) != payload
                ):
                    raise ConflictError("The completed effect has a different result")
                return
            receipt.status, receipt.encrypted_result = "completed", encrypted.ciphertext

    def uncertain(self, operation_id: str) -> None:
        with self.sessions.begin() as session:
            receipt = session.get(WorkflowOperation, operation_id, with_for_update=True)
            if (
                receipt is not None
                and receipt.run_id == self.run_id
                and receipt.status == "started"
            ):
                receipt.status = "uncertain"


def reconcile_effect(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    request: EffectResolution,
    cipher: SecretCipher,
) -> WorkflowRun:
    actor = current_user(session, actor.user_id)
    if not actor.has_platform_role("admin"):
        raise ForbiddenError("Only an administrator may reconcile workflow effects")
    row = session.get(WorkflowRun, run_id, with_for_update=True)
    receipt = session.get(WorkflowOperation, request.operation_id, with_for_update=True)
    if row is None or receipt is None or receipt.run_id != run_id:
        raise NotFoundError("The workflow effect does not exist")
    resolution = {**request.model_dump(mode="json"), "actor_id": str(actor.user_id)}
    if receipt.encrypted_reconciliation is not None:
        if cipher.decrypt_payload(receipt.encrypted_reconciliation) != resolution:
            raise ConflictError("This effect already has a different reconciliation")
        return row
    if row.status != "needs_attention" or receipt.status not in {"started", "uncertain"}:
        raise ConflictError("The workflow effect is not awaiting reconciliation")
    receipt.status = "reconciled"
    receipt.encrypted_reconciliation = cipher.encrypt_payload(resolution).ciphertext
    session.flush()
    if not session.scalar(
        select(
            exists().where(
                WorkflowOperation.run_id == run_id,
                WorkflowOperation.status.in_(("started", "uncertain")),
            )
        )
    ):
        # Linked Agent effects are independently reconciled in execution.
        from opsmesh.execution.runs.models import ExecutionRun

        linked_unknown = session.scalar(
            select(
                exists().where(
                    WorkflowNodeRun.run_id == run_id,
                    WorkflowNodeRun.execution_run_id == ExecutionRun.id,
                    ExecutionRun.status.in_(
                        ("queued", "running", "cancelling", "interrupted", "needs_attention")
                    ),
                )
            )
        )
        if not linked_unknown:
            row.status, row.error_code = "failed", "effects_reconciled"
    append_event(
        session, run_id=run_id, kind="effect_reconciled", payload=resolution, cipher=cipher
    )
    return row
