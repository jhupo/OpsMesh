"""Short product transactions around native sandbox I/O and durable effect receipts."""

import asyncio
import base64
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.orchestration.contracts import (
    CleanupScopeAdmission,
    ExecutionBindings,
    ExecutionSubject,
    ScopeAdmission,
)
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.contracts import RuntimeCommand, RuntimeResult, RuntimeStorage
from opsmesh.orchestration.runtime.models import (
    Environment,
    EnvironmentFile,
    EnvironmentOperationReceipt,
)
from opsmesh.orchestration.runtime.sandbox import SandboxBackend, SandboxCommandCancelled
from opsmesh.shared.errors import ConflictError, DomainError
from opsmesh.shared.policies import enforce_operation_policy
from opsmesh.shared.security.encryption import SecretCipher


class RuntimeActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        admission: ScopeAdmission,
        bindings: ExecutionBindings,
        cipher: SecretCipher,
        backend: SandboxBackend,
        storage: RuntimeStorage,
    ) -> None:
        self.sessions = sessions
        self.admission = admission
        self.bindings = bindings
        self.cipher = cipher
        self.backend = backend
        self.storage = storage

    def claim(self, command: RuntimeCommand) -> tuple[bool, dict[str, Any]]:
        with self.sessions() as session, session.begin():
            admission = self.admission
            if command.cleanup_ref is not None:
                if command.action != "close" or self.bindings.cleanup is None:
                    raise ConflictError("Scoped cleanup is not authorized")
                admission = CleanupScopeAdmission(self.bindings.cleanup, command.cleanup_ref)
            record = load_environment(
                session,
                environment_id=command.environment_id,
                subject=ExecutionSubject(command.principal_id),
                admission=admission,
                lock=True,
                shared_use=command.action in {"command", "read", "write"},
            )
            receipt = session.get(
                EnvironmentOperationReceipt, (record.scope_id, command.operation_id)
            )
            if receipt is None or receipt.environment_id != record.id:
                raise ConflictError("The runtime operation does not exist")
            if receipt.status == "completed":
                return False, self.cipher.decrypt_payload(
                    receipt.encrypted_result
                ) if receipt.encrypted_result else {"cancelled": True}
            if receipt.status != "pending":
                raise ConflictError("The runtime operation needs reconciliation")
            if command.action == "command":
                enforce_operation_policy(
                    session, action="runtime.command", name=str(command.arguments["command"][0])
                )
            if command.action == "write":
                content = base64.b64decode(command.arguments["content_base64"], validate=True)
                if len(content) > 4_000_000:
                    raise ConflictError("The managed transfer exceeds its byte limit")
                receipt.file_path = command.arguments["path"]
                if content:
                    receipt.storage_reservation_id = self.storage.reserve_storage(
                        session,
                        user_id=record.principal_id,
                        amount=len(content),
                        idempotency_scope="orchestration.file",
                        idempotency_key=f"{record.id}:{sha256(command.operation_id.encode()).hexdigest()}",
                    ).id
            if command.action == "create":
                reservation = self.bindings.capacity.reserve_capacity(
                    session,
                    user_id=record.principal_id,
                    kind="environment",
                    amount=1,
                    idempotency_scope="orchestration.environment",
                    idempotency_key=str(record.id),
                )
                record.capacity_reservation_id = reservation.id
                record.status = "creating"
            elif command.action == "close":
                record.status = "closing"
            else:
                record.status = "resuming"
            receipt.status = "started"
            receipt.revision += 1
            payload = (
                self.cipher.decrypt_payload(record.encrypted_state)
                if record.encrypted_state
                else dict(record.configuration)
            )
            root_directory_id = record.configuration.get("root_directory_id")
            if isinstance(root_directory_id, str):
                payload["root_directory_id"] = root_directory_id
            return True, payload

    def save_state(self, command: RuntimeCommand, state: dict[str, object]) -> None:
        with self.sessions() as session, session.begin():
            record = session.scalars(
                select(Environment)
                .where(Environment.id == command.environment_id)
                .with_for_update()
            ).one()
            if record.active_operation_id != command.operation_id:
                raise ConflictError("The environment operation no longer owns its state")
            secret = self.cipher.encrypt_payload(state)
            record.encrypted_state = secret.ciphertext
            record.encryption_key_id = secret.key_id

    def complete(self, command: RuntimeCommand, result: dict[str, object] | None) -> RuntimeResult:
        with self.sessions() as session, session.begin():
            record = session.scalars(
                select(Environment)
                .where(Environment.id == command.environment_id)
                .with_for_update()
            ).one()
            receipt = session.get(
                EnvironmentOperationReceipt, (record.scope_id, command.operation_id)
            )
            if receipt is None:
                raise ConflictError("The runtime operation disappeared")
            if receipt.status == "completed":
                return RuntimeResult(
                    operation_id=receipt.operation_id,
                    status=receipt.status,
                    result=self.cipher.decrypt_payload(receipt.encrypted_result)
                    if receipt.encrypted_result
                    else {},
                )
            if result is None:
                record.status = "needs_attention"
                receipt.status = "uncertain"
            else:
                receipt.status = "completed"
                encrypted = self.cipher.encrypt_payload(result)
                receipt.encrypted_result = encrypted.ciphertext
                receipt.encryption_key_id = encrypted.key_id
                record.status = "closed" if command.action == "close" else "ready"
                record.active_operation_id = None
                if command.action == "write":
                    settle_file(session, record, receipt, result, self.storage)
                elif command.action == "close":
                    reclaim_files(session, record.id, self.storage)
                if command.action == "close" and record.capacity_reservation_id is not None:
                    self.bindings.capacity.release_capacity(
                        session,
                        user_id=record.principal_id,
                        reservation_id=record.capacity_reservation_id,
                    )
                    record.capacity_reservation_id = None
            receipt.revision += 1
            return RuntimeResult(
                operation_id=command.operation_id, status=receipt.status, result=result
            )

    @activity.defn(name="orchestration.runtime")
    async def execute(self, command: RuntimeCommand) -> RuntimeResult:
        try:
            claimed, payload = await asyncio.to_thread(self.claim, command)
        except DomainError as exc:
            await asyncio.to_thread(self.reject_pending, command, exc.code)
            raise
        if not claimed:
            return RuntimeResult(
                operation_id=command.operation_id, status="completed", result=payload
            )
        try:
            if command.action == "create":
                native = await self.backend.create(
                    image=str(payload["image"]),
                    labels={
                        "opsmesh.environment": str(command.environment_id),
                        "opsmesh.principal": str(command.principal_id),
                        **(
                            {"opsmesh.directory": str(payload["root_directory_id"])}
                            if payload.get("root_directory_id")
                            else {}
                        ),
                    },
                )
                await asyncio.to_thread(self.save_state, command, self.backend.state(native))
            else:
                native = await self.backend.resume(payload)
            result = await self.backend.execute(native, command.action, command.arguments)
            if command.action != "close":
                await asyncio.to_thread(self.save_state, command, self.backend.state(native))
            return await asyncio.to_thread(self.complete, command, result)
        except SandboxCommandCancelled as exc:
            await asyncio.shield(asyncio.to_thread(self.complete, command, exc.result))
            raise
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self.complete, command, None))
            raise

    def reject_pending(self, command: RuntimeCommand, error_code: str) -> None:
        with self.sessions() as session, session.begin():
            record = session.scalar(
                select(Environment)
                .where(Environment.id == command.environment_id)
                .with_for_update()
            )
            if record is None:
                return
            receipt = session.get(
                EnvironmentOperationReceipt, (record.scope_id, command.operation_id)
            )
            if receipt is None or receipt.status != "pending":
                return
            receipt.status = "completed"
            receipt.revision += 1
            encrypted = self.cipher.encrypt_payload(
                {"error_code": error_code, "not_executed": True}
            )
            receipt.encrypted_result, receipt.encryption_key_id = (
                encrypted.ciphertext,
                encrypted.key_id,
            )
            if record.active_operation_id == command.operation_id:
                record.active_operation_id = None
                record.status = "closed" if command.action == "create" else "ready"


def settle_file(
    session: Session,
    environment: Environment,
    receipt: EnvironmentOperationReceipt,
    result: dict[str, object],
    storage: RuntimeStorage,
) -> None:
    size = result.get("bytes_written")
    if not isinstance(size, int) or size < 0 or receipt.file_path is None:
        raise ConflictError("The native write did not confirm its stored byte count")
    previous = session.get(EnvironmentFile, (environment.id, receipt.file_path))
    if receipt.storage_reservation_id is not None:
        storage.settle_storage(
            session, reservation_id=receipt.storage_reservation_id, actual_amount=size
        )
    if previous is not None:
        if previous.storage_reservation_id is not None:
            storage.reclaim_storage(session, reservation_id=previous.storage_reservation_id)
        previous.storage_reservation_id, previous.size = receipt.storage_reservation_id, size
    else:
        session.add(
            EnvironmentFile(
                environment_id=environment.id,
                path=receipt.file_path,
                storage_reservation_id=receipt.storage_reservation_id,
                size=size,
            )
        )


def reclaim_files(session: Session, environment_id: UUID, storage: RuntimeStorage) -> None:
    for stored in session.scalars(
        select(EnvironmentFile)
        .where(EnvironmentFile.environment_id == environment_id)
        .order_by(EnvironmentFile.path)
    ):
        if stored.storage_reservation_id is not None:
            storage.reclaim_storage(session, reservation_id=stored.storage_reservation_id)
        session.delete(stored)
