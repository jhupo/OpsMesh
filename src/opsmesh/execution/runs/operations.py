"""Encrypted effect receipts and quota settlement shared by execution adapters."""

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from opsmesh.execution.context import ExecutionContext
from opsmesh.execution.runs.access import authorize_run
from opsmesh.execution.runs.events import append_event
from opsmesh.execution.runs.models import ExecutionOperation, ExecutionRun
from opsmesh.platform.billing import service as billing
from opsmesh.platform.billing.contracts import ModelCostRequest
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.quotas import service as quotas


@dataclass(frozen=True)
class OperationLedger:
    sessions: sessionmaker[Session]
    cipher: SecretCipher
    context: ExecutionContext

    def authorize(self) -> None:
        with self.sessions.begin() as session:
            row = authorize_run(session, self.context.run_id)
            if (
                row.instance_id != self.context.instance_id
                or row.attempt_id != self.context.attempt_id
            ):
                raise ConflictError("The execution attempt no longer owns this run")

    def claim(
        self,
        operation_id: str,
        kind: str,
        payload: dict[str, object],
        *,
        tokens: int | None = None,
        cost: ModelCostRequest | None = None,
    ) -> dict[str, object] | None:
        if len(canonical_json(payload).encode()) > 1_000_000:
            raise InvalidArgumentError("The operation input exceeds its size limit")
        if tokens is not None and cost is None:
            raise InvalidArgumentError("Model operations require a cost admission description")
        fingerprint = payload_digest(payload)
        with self.sessions.begin() as session:
            row = authorize_run(session, self.context.run_id, lock=True)
            if (
                row.attempt_id != self.context.attempt_id
                or row.instance_id != self.context.instance_id
            ):
                raise ConflictError("The execution attempt no longer owns this run")
            previous = session.get(ExecutionOperation, operation_id, with_for_update=True)
            if previous is not None:
                if (
                    previous.run_id != row.id
                    or previous.fingerprint != fingerprint
                    or previous.status != "completed"
                    or previous.encrypted_result is None
                ):
                    raise ConflictError("The operation requires reconciliation")
                return self.cipher.decrypt_payload(previous.encrypted_result)
            reservation = (
                quotas.reserve_tokens(
                    session,
                    user_id=row.user_id,
                    amount=tokens,
                    idempotency_scope="model-operation",
                    idempotency_key=operation_id,
                )
                if tokens is not None
                else None
            )
            if tokens is not None:
                assert cost is not None
                quote = cost
                billing.reserve(
                    session,
                    operation_id=operation_id,
                    run_id=row.id,
                    user_id=row.user_id,
                    **quote.model_dump(exclude={"pricing"}),
                    pricing=quote.pricing,
                )
            session.add(
                ExecutionOperation(
                    id=operation_id,
                    run_id=row.id,
                    kind=kind,
                    fingerprint=fingerprint,
                    status="started",
                    reservation_id=reservation.id if reservation else None,
                )
            )
            append_event(
                session,
                run_id=row.id,
                kind="operation_started",
                cipher=self.cipher,
                payload={"operation_id": operation_id, "kind": kind},
            )
        return None

    def complete(
        self, operation_id: str, result: dict[str, object], *, tokens: int | None = None
    ) -> None:
        if len(canonical_json(result).encode()) > 4_000_000:
            raise InvalidArgumentError("The operation result exceeds its size limit")
        encrypted = self.cipher.encrypt_payload(result)
        with self.sessions.begin() as session:
            row = session.get(ExecutionRun, self.context.run_id, with_for_update=True)
            if row is None:
                raise ConflictError("The run does not exist")
            receipt = session.get(ExecutionOperation, operation_id, with_for_update=True)
            if receipt is None or receipt.run_id != self.context.run_id:
                raise ConflictError("The operation receipt does not exist")
            if receipt.status == "completed":
                return
            if receipt.status not in {"started", "uncertain"}:
                raise ConflictError("The operation has already been reconciled")
            if receipt.reservation_id is not None:
                if tokens is None:
                    raise InvalidArgumentError("Model completion requires native usage")
                quotas.settle_tokens(
                    session, reservation_id=receipt.reservation_id, actual_amount=tokens
                )
                usage = result.get("usage")
                if not isinstance(usage, dict):
                    raise InvalidArgumentError("Model completion requires detailed token usage")
                billing.settle(
                    session,
                    operation_id,
                    input_tokens=int(usage["input_tokens"]),
                    output_tokens=int(usage["output_tokens"]),
                )
            receipt.status = "completed"
            receipt.encrypted_result, receipt.key_id = encrypted.ciphertext, encrypted.key_id
            append_event(
                session,
                run_id=row.id,
                kind="operation_completed",
                cipher=self.cipher,
                payload={
                    "operation_id": operation_id,
                    "kind": receipt.kind,
                    "usage": result.get("usage")
                    if receipt.kind in {"model", "inference"}
                    else None,
                },
            )

    def uncertain(self, operation_id: str, *, error_code: str | None = None) -> None:
        with self.sessions.begin() as session:
            row = session.get(ExecutionRun, self.context.run_id, with_for_update=True)
            if row is None:
                return
            receipt = session.get(ExecutionOperation, operation_id, with_for_update=True)
            if (
                receipt is not None
                and receipt.run_id == self.context.run_id
                and receipt.status == "started"
            ):
                receipt.status = "uncertain"
                append_event(
                    session,
                    run_id=row.id,
                    kind="operation_uncertain",
                    cipher=self.cipher,
                    payload={
                        "operation_id": operation_id,
                        "kind": receipt.kind,
                        "error_code": error_code,
                    },
                )
