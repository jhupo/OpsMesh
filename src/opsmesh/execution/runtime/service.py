"""Persistent sandbox operations; dispatch is supplied by the application."""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from agents.sandbox.session import SandboxSession
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.execution.runtime.access import get_environment
from opsmesh.execution.runtime.models import SandboxEnvironment, SandboxOperation
from opsmesh.execution.runtime.schemas import (
    EnvironmentCreate,
    EnvironmentReconciliation,
    OperationCreate,
    RuntimeCommand,
    SandboxCommandCancelled,
    SandboxDirectory,
)
from opsmesh.resources.directories import service as directories
from opsmesh.resources.storage.base import LocalDirectoryStorage
from opsmesh.shared.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
)
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest
from opsmesh.users import service as users
from opsmesh.users.access import CurrentUser, ensure_active_user
from opsmesh.users.quotas import service as quotas

if TYPE_CHECKING:
    from opsmesh.execution.runtime.sandbox import SandboxBackend


def current_actor(session: Session, actor: CurrentUser) -> CurrentUser:
    current = users.load_identity(session, actor.user_id)
    if current is None:
        raise ForbiddenError("The runtime user is unavailable")
    return ensure_active_user(current)


def create_environment(
    session: Session,
    *,
    actor: CurrentUser,
    request: EnvironmentCreate,
    cipher: SecretCipher,
    allowed_images: Collection[str],
) -> SandboxEnvironment:
    current_actor(session, actor)
    if request.image not in allowed_images:
        raise ForbiddenError("The sandbox image is not registered by the operator")
    directory = (
        directories.ensure_default_directory(session, actor=actor)
        if request.directory_id is None
        else directories.get_directory(
            session,
            actor=actor,
            directory_id=request.directory_id,
            permission="use",
            lock=True,
        )
    )
    # A persistent writable mount must never outlive another user's revoked grant.
    # Shared directory attachment needs explicit runtime revocation support first.
    if directory.owner_user_id != actor.user_id:
        raise ForbiddenError("Persistent sandboxes currently require an owned directory")
    environment_id = uuid4()
    reservation = quotas.reserve_capacity(
        session,
        user_id=actor.user_id,
        kind="environment",
        amount=1,
        idempotency_scope="sandbox-environment",
        idempotency_key=str(environment_id),
    )
    environment = SandboxEnvironment(
        id=environment_id,
        owner_user_id=actor.user_id,
        directory_id=directory.id,
        image=request.image,
        status="pending",
        capacity_reservation_id=reservation.id,
    )
    session.add(environment)
    session.flush()
    _stage(session, environment, actor, "create", {}, "create", cipher)
    return environment


def request_operation(
    session: Session,
    *,
    actor: CurrentUser,
    environment_id: UUID,
    request: OperationCreate,
    cipher: SecretCipher,
) -> SandboxOperation:
    environment = get_environment(session, actor=actor, environment_id=environment_id, lock=True)
    arguments = request.model_dump(
        mode="json", exclude={"action", "request_key"}, exclude_none=True
    )
    fingerprint = payload_digest({"action": request.action, "arguments": arguments})
    existing = session.scalar(
        select(SandboxOperation).where(
            SandboxOperation.environment_id == environment.id,
            SandboxOperation.request_key == request.request_key,
        )
    )
    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise ConflictError("The operation key was used with different input")
        return existing
    if environment.active_run_id is not None:
        raise ConflictError("The sandbox is in use by an unfinished Run")
    if environment.status != "ready" or environment.active_operation_id is not None:
        raise ConflictError("The sandbox is busy, closed or requires reconciliation")
    return _stage(
        session, environment, actor, request.action, arguments, request.request_key, cipher
    )


def reconcile_environment(
    session: Session,
    *,
    actor: CurrentUser,
    environment_id: UUID,
    request: EnvironmentReconciliation,
    cipher: SecretCipher,
) -> SandboxOperation:
    actor = current_actor(session, actor)
    if not actor.has_platform_role("admin"):
        raise ForbiddenError("Only an administrator may reconcile an environment")
    environment = session.get(SandboxEnvironment, environment_id, with_for_update=True)
    if environment is None:
        raise NotFoundError("The environment does not exist")
    arguments: dict[str, object] = {"reconciliation": request.evidence}
    previous = session.scalar(
        select(SandboxOperation).where(
            SandboxOperation.environment_id == environment_id,
            SandboxOperation.request_key == request.request_key,
        )
    )
    if previous is not None:
        if previous.fingerprint != payload_digest({"action": "close", "arguments": arguments}):
            raise ConflictError("The reconciliation key was reused")
        return previous
    if environment.status != "needs_attention" or environment.active_run_id is not None:
        raise ConflictError("Reconcile unfinished runs before closing the environment")
    return _stage(session, environment, actor, "close", arguments, request.request_key, cipher)


def _stage(
    session: Session,
    environment: SandboxEnvironment,
    actor: CurrentUser,
    action: str,
    arguments: dict[str, object],
    request_key: str,
    cipher: SecretCipher,
) -> SandboxOperation:
    encrypted = cipher.encrypt_payload(arguments)
    operation = SandboxOperation(
        id=uuid4(),
        environment_id=environment.id,
        requested_by_user_id=actor.user_id,
        request_key=request_key,
        action=action,
        fingerprint=payload_digest(
            {
                "action": action,
                "arguments": arguments,
            }
        ),
        status="pending",
        encrypted_arguments=encrypted.ciphertext,
        arguments_key_id=encrypted.key_id,
    )
    session.add(operation)
    environment.active_operation_id = operation.id
    environment.status = "busy"
    session.flush()
    return operation


def get_operation(
    session: Session,
    *,
    actor: CurrentUser,
    environment_id: UUID,
    operation_id: UUID,
) -> SandboxOperation:
    get_environment(session, actor=actor, environment_id=environment_id)
    operation = session.get(SandboxOperation, operation_id)
    if operation is None or operation.environment_id != environment_id:
        raise NotFoundError("The sandbox operation does not exist")
    return operation


@dataclass(frozen=True)
class OperationTarget:
    environment_id: UUID
    user_id: UUID
    directory_id: UUID
    image: str
    action: str
    arguments: dict[str, Any] = field(repr=False)
    state: dict[str, object] | None = field(repr=False)


class SandboxOperations:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        backend: SandboxBackend,
        allowed_images: Collection[str],
    ) -> None:
        self.sessions, self.cipher, self.backend = sessions, cipher, backend
        self.allowed_images = frozenset(allowed_images)

    def _records(
        self,
        session: Session,
        command: RuntimeCommand,
    ) -> tuple[SandboxEnvironment, SandboxOperation]:
        environment = session.get(SandboxEnvironment, command.environment_id, with_for_update=True)
        operation = session.get(SandboxOperation, command.execution_id, with_for_update=True)
        if environment is None or operation is None or operation.environment_id != environment.id:
            raise ConflictError("The sandbox command does not match a stored operation")
        return environment, operation

    def claim(self, command: RuntimeCommand) -> OperationTarget | str:
        with self.sessions.begin() as session:
            environment, operation = self._records(session, command)
            if operation.status in {"completed", "rejected"}:
                return operation.status
            if operation.status != "pending" or environment.active_operation_id != operation.id:
                raise ConflictError("The sandbox operation requires reconciliation")
            reconciliation = False
            try:
                actor = users.load_identity(session, operation.requested_by_user_id)
                arguments = self.cipher.decrypt_payload(
                    operation.encrypted_arguments, key_id=operation.arguments_key_id
                )
                reconciliation = operation.action == "close" and "reconciliation" in arguments
                if actor is None or (
                    actor.user_id != environment.owner_user_id
                    and not (reconciliation and actor.has_platform_role("admin"))
                ):
                    raise ForbiddenError("The sandbox owner is unavailable")
                current_actor(session, actor)
                if operation.action != "close":
                    images = self.allowed_images
                    if environment.image not in images:
                        raise ForbiddenError("The sandbox image is no longer registered")
                    directory = directories.get_directory(
                        session,
                        actor=actor,
                        directory_id=environment.directory_id,
                        permission="use",
                    )
                    if directory.owner_user_id != actor.user_id:
                        raise ForbiddenError("The persistent directory owner changed")
            except DomainError:
                operation.status, operation.error_code = "rejected", "authorization_revoked"
                environment.active_operation_id = None
                environment.status = (
                    "closed"
                    if operation.action == "create"
                    else "needs_attention"
                    if reconciliation
                    else "ready"
                )
                if operation.action == "create":
                    quotas.release_capacity(
                        session,
                        user_id=environment.owner_user_id,
                        reservation_id=environment.capacity_reservation_id,
                    )
                return "rejected"
            operation.status = "started"
            return OperationTarget(
                environment.id,
                environment.owner_user_id,
                environment.directory_id,
                environment.image,
                operation.action,
                self.cipher.decrypt_payload(
                    operation.encrypted_arguments, key_id=operation.arguments_key_id
                ),
                self.cipher.decrypt_payload(
                    environment.encrypted_state, key_id=environment.state_key_id
                )
                if environment.encrypted_state
                else None,
            )

    def complete(
        self,
        command: RuntimeCommand,
        result: dict[str, object],
        state: dict[str, object] | None,
    ) -> str:
        encrypted = self.cipher.encrypt_payload(result)
        native_state = self.cipher.encrypt_payload(state) if state is not None else None
        with self.sessions.begin() as session:
            environment, operation = self._records(session, command)
            if operation.status == "completed":
                return "completed"
            if operation.status != "started" or environment.active_operation_id != operation.id:
                raise ConflictError("The sandbox operation is no longer owned by this execution")
            operation.status = "completed"
            operation.encrypted_result, operation.result_key_id = (
                encrypted.ciphertext,
                encrypted.key_id,
            )
            environment.active_operation_id = None
            environment.status = "closed" if operation.action == "close" else "ready"
            if native_state is not None:
                environment.encrypted_state, environment.state_key_id = (
                    native_state.ciphertext,
                    native_state.key_id,
                )
            if operation.action == "close":
                for abandoned in session.scalars(
                    select(SandboxOperation)
                    .where(
                        SandboxOperation.environment_id == environment.id,
                        SandboxOperation.id != operation.id,
                        SandboxOperation.status.in_(("started", "uncertain")),
                    )
                    .with_for_update()
                ):
                    abandoned.status = "rejected"
                    abandoned.error_code = "environment_closed_after_unknown_effect"
                environment.encrypted_state = environment.state_key_id = None
                quotas.release_capacity(
                    session,
                    user_id=environment.owner_user_id,
                    reservation_id=environment.capacity_reservation_id,
                )
            return "completed"

    def uncertain(self, command: RuntimeCommand) -> None:
        with self.sessions.begin() as session:
            environment, operation = self._records(session, command)
            if operation.status != "started":
                return
            operation.status, operation.error_code = "uncertain", "external_effect_unknown"
            environment.status = "needs_attention"
            # Retain both operation ownership and the environment reservation.

    def save_native_state(self, command: RuntimeCommand, state: dict[str, object]) -> None:
        """Preserve the resource identity even if later initialization fails."""
        encrypted = self.cipher.encrypt_payload(state)
        with self.sessions.begin() as session:
            environment, operation = self._records(session, command)
            if operation.status != "started" or environment.active_operation_id != operation.id:
                raise ConflictError("The sandbox operation lost ownership")
            environment.encrypted_state, environment.state_key_id = (
                encrypted.ciphertext,
                encrypted.key_id,
            )

    async def operate(self, command: RuntimeCommand) -> str:
        # Claim/complete are short synchronous product transactions. They do not
        # span awaits, so cancellation cannot leave a background commit unnoticed.
        target = self.claim(command)
        if isinstance(target, str):
            return target
        native: SandboxSession | None = None
        try:
            if target.action == "create":
                native = await self.backend.create(
                    image=target.image,
                    labels={
                        "opsmesh.environment": str(target.environment_id),
                        "opsmesh.principal": str(target.user_id),
                        "opsmesh.directory": str(target.directory_id),
                    },
                )
                self.save_native_state(command, self.backend.state(native))
                await self.backend.prepare_directory(native)
                await self.backend.initialize_directory(native)
                result: dict[str, object] = {}
            else:
                if target.state is None:
                    raise ConflictError("The sandbox has no resumable native state")
                if target.action == "close":
                    await self.backend.delete_state(target.state)
                    return self.complete(command, {}, None)
                native = await self.backend.resume(target.state)
                try:
                    result = await self.backend.execute(native, target.action, target.arguments)
                except SandboxCommandCancelled as cancelled:
                    self.complete(command, cancelled.result, self.backend.state(native))
                    raise
            state = None if target.action == "close" else self.backend.state(native)
            return self.complete(command, result, state)
        except BaseException as error:
            self.uncertain(command)
            if isinstance(error, asyncio.CancelledError):
                raise
            # SDK errors can contain command text, private paths or file data.
            # The stored receipt is the diagnostic reference released to clients.
            raise ConflictError("The sandbox operation requires reconciliation") from None


class SandboxDirectories:
    """Concrete directory authority, reading only new users/resources/runtime facts."""

    def __init__(self, sessions: sessionmaker[Session], filesystem: LocalDirectoryStorage) -> None:
        self.sessions, self.filesystem = sessions, filesystem

    def __call__(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        directory_id: UUID | None,
    ) -> SandboxDirectory:
        with self.sessions.begin() as session:
            environment = session.get(SandboxEnvironment, environment_id)
            actor = users.load_identity(session, principal_id)
            if (
                environment is None
                or actor is None
                or environment.owner_user_id != principal_id
                or environment.directory_id != directory_id
            ):
                raise ForbiddenError("The sandbox directory identity is unavailable")
            mount = directories.resolve_directory(
                session,
                actor=actor,
                directory_id=environment.directory_id,
                filesystem=self.filesystem,
                writable=True,
            )
            return SandboxDirectory(mount.directory_id, mount.host_path)
