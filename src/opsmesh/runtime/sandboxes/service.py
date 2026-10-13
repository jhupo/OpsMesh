from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from agents.sandbox import Manifest
from agents.sandbox.session import SandboxSession, SandboxSessionState
from agents.sandbox.snapshot import SnapshotSpec
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.registry import (
    SandboxBackend,
    SandboxBackendRegistry,
    native_resource_identity,
)
from opsmesh.shared.errors import ConflictError, NotFoundError, PolicyDeniedError
from opsmesh.shared.security.secrets import SecretEncryptionService

SandboxOwnerKind = Literal["run", "environment", "mcp", "command", "plugin"]


@dataclass(frozen=True)
class SandboxReferenceSnapshot:
    id: UUID
    runtime_id: UUID
    backend_id: str
    native_session_id: str | None
    status: str
    encrypted_state: str | None
    encryption_key_id: str | None


class SandboxService:
    """Product references with short DB transactions around native SDK operations."""

    def __init__(
        self,
        sessions: Callable[[], AbstractContextManager[Session]],
        backends: SandboxBackendRegistry,
        encryption: SecretEncryptionService,
    ) -> None:
        self._sessions = sessions
        self._backends = backends
        self._encryption = encryption

    async def create(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        owner_kind: SandboxOwnerKind,
        owner_id: UUID,
        manifest: Manifest,
        scope_id: UUID | None = None,
        snapshot: SnapshotSpec | None = None,
    ) -> SandboxReferenceSnapshot:
        reservation = asyncio.create_task(
            asyncio.to_thread(
                self._reserve, workspace_id, runtime_id, owner_kind, owner_id, scope_id
            )
        )
        try:
            reference, options, created = await asyncio.shield(reservation)
        except asyncio.CancelledError:
            reference, _, created = await reservation
            if created:
                await asyncio.to_thread(
                    self._mark,
                    workspace_id,
                    reference.id,
                    "deleted",
                    "sandbox_cancelled_before_creation",
                )
            raise
        if not created:
            return reference
        backend = self._backends.require(reference.backend_id)
        native_session: SandboxSession | None = None
        try:
            native_session = await backend.client.create(
                manifest=manifest, options=backend.parse_options(options), snapshot=snapshot
            )
            await asyncio.to_thread(self._save_state, workspace_id, reference.id, native_session)
            await native_session.start()
            await asyncio.to_thread(self._require_active, workspace_id, runtime_id)
            await asyncio.to_thread(
                self._save_state, workspace_id, reference.id, native_session, "running"
            )
            return await asyncio.to_thread(self._load, workspace_id, reference.id)
        except BaseException:
            if native_session is None:
                await asyncio.shield(
                    asyncio.to_thread(
                        self._mark,
                        workspace_id,
                        reference.id,
                        "unknown",
                        "sandbox_create_outcome_unknown",
                    )
                )
                raise
            try:
                await asyncio.shield(backend.client.delete(native_session))
            except BaseException:
                await asyncio.shield(
                    asyncio.to_thread(
                        self._mark,
                        workspace_id,
                        reference.id,
                        "unknown",
                        "sandbox_create_cleanup_failed",
                    )
                )
                raise
            await asyncio.shield(
                asyncio.to_thread(
                    self._mark, workspace_id, reference.id, "deleted", "sandbox_start_failed"
                )
            )
            raise

    async def resume(self, workspace_id: UUID, reference_id: UUID) -> SandboxSession:
        reference = await asyncio.to_thread(self._load, workspace_id, reference_id, True)
        if reference.status not in {"running", "stopped"}:
            raise ConflictError("Sandbox is unavailable for resume")
        backend, state = self._native_state(reference)
        original_resource = native_resource_identity(state)
        native_session = await backend.client.resume(state)
        try:
            await native_session.start()
            await asyncio.to_thread(self._require_active, workspace_id, reference.runtime_id)
            await asyncio.to_thread(
                self._save_state, workspace_id, reference_id, native_session, "running"
            )
        except BaseException:
            if native_resource_identity(native_session.state) != original_resource:
                await asyncio.shield(backend.client.delete(native_session))
            raise
        return native_session

    async def stop(self, workspace_id: UUID, reference_id: UUID) -> None:
        reference = await asyncio.to_thread(self._load, workspace_id, reference_id)
        if reference.status == "stopped":
            return
        native_session = await self.resume(workspace_id, reference_id)
        await native_session.stop()
        await asyncio.to_thread(
            self._save_state, workspace_id, reference_id, native_session, "stopped"
        )

    async def delete(self, workspace_id: UUID, reference_id: UUID) -> None:
        # Cancellation must not strand the committed deletion claim while its DB
        # thread or native provider operation is still running.
        deletion = asyncio.create_task(self._delete(workspace_id, reference_id))
        try:
            await asyncio.shield(deletion)
        except asyncio.CancelledError:
            await deletion
            raise

    async def reconcile_delete(self, workspace_id: UUID, reference_id: UUID) -> None:
        """An explicit operator decision may delete uncertain resources with saved SDK state."""
        deletion = asyncio.create_task(self._delete(workspace_id, reference_id, True))
        try:
            await asyncio.shield(deletion)
        except asyncio.CancelledError:
            await deletion
            raise

    async def _delete(
        self, workspace_id: UUID, reference_id: UUID, reconcile: bool = False
    ) -> None:
        reference = await asyncio.to_thread(
            self._claim_delete, workspace_id, reference_id, reconcile
        )
        if reference is None:
            return
        try:
            backend, state = self._native_state(reference)
            native_session = await backend.client.resume(state)
            await backend.client.delete(native_session)
        except BaseException:
            await asyncio.shield(
                asyncio.to_thread(
                    self._mark, workspace_id, reference_id, "unknown", "sandbox_delete_failed"
                )
            )
            raise
        await asyncio.to_thread(self._mark, workspace_id, reference_id, "deleted", None)

    async def read_file(
        self, workspace_id: UUID, reference_id: UUID, relative_path: str, *, max_bytes: int
    ) -> bytes:
        native_session = await self.resume(workspace_id, reference_id)
        path = self._workspace_path(native_session, relative_path)
        with await native_session.read(path) as stream:
            payload = stream.read(max_bytes + 1)
        if not isinstance(payload, bytes):
            raise TypeError("Native sandbox returned a non-binary file")
        if len(payload) > max_bytes:
            raise PolicyDeniedError("Sandbox file exceeds the product transfer limit")
        return payload

    def _reserve(
        self,
        workspace_id: UUID,
        runtime_id: UUID,
        owner_kind: SandboxOwnerKind,
        owner_id: UUID,
        scope_id: UUID | None,
    ) -> tuple[SandboxReferenceSnapshot, dict[str, object], bool]:
        with self._sessions() as session:
            runtime = session.scalar(
                select(WorkspaceRuntime)
                .where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.id == runtime_id,
                    WorkspaceRuntime.status == "active",
                )
                .with_for_update()
            )
            if runtime is None:
                raise PolicyDeniedError("Execution environment is unavailable")
            existing = session.scalar(
                select(SandboxReference)
                .where(
                    SandboxReference.workspace_id == workspace_id,
                    SandboxReference.owner_kind == owner_kind,
                    SandboxReference.owner_id == owner_id,
                )
                .with_for_update()
            )
            options = dict(runtime.sandbox_options)
            self._backends.require(runtime.sandbox_backend_id).parse_options(options)
            if existing is not None:
                if existing.workspace_runtime_id != runtime_id or existing.scope_id != scope_id:
                    raise ConflictError("Sandbox owner is bound to another environment or scope")
                if existing.status not in {"running", "stopped"}:
                    raise ConflictError("Sandbox outcome requires reconciliation")
                return self._reference(existing), options, False
            reference = SandboxReference(
                workspace_id=workspace_id,
                workspace_runtime_id=runtime_id,
                owner_kind=owner_kind,
                owner_id=owner_id,
                scope_id=scope_id,
                backend_id=runtime.sandbox_backend_id,
                status="provisioning",
            )
            session.add(reference)
            session.flush()
            result = self._reference(reference)
            session.commit()
            return result, options, True

    def _load(
        self, workspace_id: UUID, reference_id: UUID, require_active: bool = False
    ) -> SandboxReferenceSnapshot:
        with self._sessions() as session:
            reference = self._require_reference(session, workspace_id, reference_id)
            if require_active:
                self._environment(session, workspace_id, reference.workspace_runtime_id)
            return self._reference(reference)

    def _require_active(self, workspace_id: UUID, runtime_id: UUID) -> None:
        with self._sessions() as session:
            self._environment(session, workspace_id, runtime_id)

    def _claim_delete(
        self, workspace_id: UUID, reference_id: UUID, reconcile: bool = False
    ) -> SandboxReferenceSnapshot | None:
        with self._sessions() as session:
            reference = self._require_reference(session, workspace_id, reference_id)
            if reference.status == "deleted":
                return None
            if reference.status == "deleting" and not reconcile:
                raise ConflictError("Sandbox deletion is already in progress")
            if reference.encrypted_session_state is None or (
                reference.status == "unknown" and not reconcile
            ):
                raise ConflictError("Sandbox deletion requires reconciliation of native state")
            result = self._reference(reference)
            reference.status = "deleting"
            session.commit()
            return result

    def _save_state(
        self,
        workspace_id: UUID,
        reference_id: UUID,
        native_session: SandboxSession,
        status: str | None = None,
    ) -> None:
        self.save_session_state(workspace_id, reference_id, native_session.state, status)

    def save_session_state(
        self,
        workspace_id: UUID,
        reference_id: UUID,
        state: SandboxSessionState,
        status: str | None = None,
    ) -> None:
        with self._sessions() as session:
            reference = self._require_reference(session, workspace_id, reference_id)
            if reference.status in {"deleted", "deleting", "unknown"}:
                raise ConflictError("Sandbox lifecycle changed during native execution")
            backend = self._backends.require(reference.backend_id)
            if state.type != reference.backend_id:
                raise PolicyDeniedError("Sandbox backend does not match its product reference")
            encrypted = self._encryption.encrypt_payload(
                backend.client.serialize_session_state(state)
            )
            reference.native_session_id = str(state.session_id)
            reference.encrypted_session_state = encrypted.ciphertext
            reference.encryption_key_id = encrypted.key_id
            if status is not None:
                reference.status = status
                reference.last_error_code = None
            session.commit()

    def _mark(self, workspace_id: UUID, reference_id: UUID, status: str, error: str | None) -> None:
        with self._sessions() as session:
            reference = self._require_reference(session, workspace_id, reference_id)
            reference.status = status
            reference.last_error_code = error
            if status == "deleted":
                reference.encrypted_session_state = None
                reference.completed_at = datetime.now(UTC)
            session.commit()

    def _native_state(
        self, reference: SandboxReferenceSnapshot
    ) -> tuple[SandboxBackend, SandboxSessionState]:
        if reference.encrypted_state is None:
            raise ConflictError("Sandbox has no saved native session state")
        backend = self._backends.require(reference.backend_id)
        payload = self._encryption.decrypt_payload(
            reference.encrypted_state, key_id=reference.encryption_key_id
        )
        state = backend.client.deserialize_session_state(payload)
        if str(state.session_id) != reference.native_session_id:
            raise PolicyDeniedError("Sandbox state does not match its product reference")
        return backend, state

    @staticmethod
    def _require_reference(
        session: Session, workspace_id: UUID, reference_id: UUID
    ) -> SandboxReference:
        reference = session.scalar(
            select(SandboxReference)
            .where(
                SandboxReference.workspace_id == workspace_id,
                SandboxReference.id == reference_id,
            )
            .with_for_update()
        )
        if reference is None:
            raise NotFoundError("Sandbox reference not found")
        return reference

    @staticmethod
    def _environment(session: Session, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime:
        runtime = session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == runtime_id,
            )
        )
        if runtime is None:
            raise NotFoundError("Execution environment not found")
        if runtime.status != "active":
            raise PolicyDeniedError("Execution environment is disabled")
        return runtime

    @staticmethod
    def _reference(reference: SandboxReference) -> SandboxReferenceSnapshot:
        return SandboxReferenceSnapshot(
            reference.id,
            reference.workspace_runtime_id,
            reference.backend_id,
            reference.native_session_id,
            reference.status,
            reference.encrypted_session_state,
            reference.encryption_key_id,
        )

    @staticmethod
    def _workspace_path(native_session: SandboxSession, relative_path: str) -> Path:
        path = PurePosixPath(relative_path)
        if not relative_path or path.is_absolute() or ".." in path.parts or "\\" in relative_path:
            raise PolicyDeniedError("Sandbox file path must stay inside the workspace")
        return Path(native_session.state.manifest.root) / relative_path
