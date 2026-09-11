from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.self_hosted import (
    EnrollmentTokenCreateRequest,
    RuntimeRegistrationRequest,
    WorkerHeartbeatRequest,
)
from backend.app.core.config import Settings
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.attestation import (
    CapabilityAttestationResult,
    evaluate_capability_attestation,
)
from backend.app.self_hosted.events import SelfHostedEventRecorder
from backend.app.self_hosted.models import (
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedWorker,
)
from backend.app.self_hosted.types import (
    AuthenticatedWorker,
    CreatedEnrollmentToken,
    RegisteredRuntime,
)


class SelfHostedIdentityService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        events: SelfHostedEventRecorder,
    ) -> None:
        self._session = session
        self._settings = settings
        self._events = events

    def create_enrollment_token(
        self,
        workspace_id: UUID,
        user_id: UUID,
        data: EnrollmentTokenCreateRequest,
    ) -> CreatedEnrollmentToken:
        token = f"ccrt_{token_urlsafe(32)}"
        record = RuntimeEnrollmentToken(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            token_hash=self.hash_token(token),
            name=data.name,
            expires_at=data.expires_at,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return CreatedEnrollmentToken(record=record, token=token)

    def register_runtime(self, data: RuntimeRegistrationRequest) -> RegisteredRuntime:
        token = self._consume_enrollment_token(data.enrollment_token)
        now = datetime.now(UTC)
        capabilities = self.validated_capabilities(
            token.workspace_id,
            data.capabilities,
        )
        attestation = evaluate_capability_attestation(
            workspace_id=token.workspace_id,
            machine_id=data.machine_id,
            capabilities=capabilities,
            attestation=data.attestation,
            settings=self._settings,
        )
        runtime_space_id = self.registration_runtime_space_id(token.workspace_id, capabilities)
        runtime = WorkspaceRuntime(
            workspace_id=token.workspace_id,
            runtime_space_id=runtime_space_id,
            runtime_provider="self_hosted",
            runtime_type="self_hosted",
            name=data.name,
            status="active",
            connection_status="online",
            capabilities=capabilities,
            last_heartbeat_at=now,
        )
        self._session.add(runtime)
        self._session.flush()
        credential_token = f"ccwc_{token_urlsafe(32)}"
        credential = RuntimeCredential(
            workspace_id=token.workspace_id,
            workspace_runtime_id=runtime.id,
            token_hash=self.hash_token(credential_token),
            last_used_at=now,
        )
        worker = SelfHostedWorker(
            workspace_id=token.workspace_id,
            workspace_runtime_id=runtime.id,
            name=data.name,
            machine_id=data.machine_id,
            version=data.version,
            capabilities=capabilities,
            capability_attestation_state=attestation.state,
            capability_attestation_fingerprint=attestation.fingerprint,
            capability_attestation_metadata=attestation.metadata,
            capability_attested_at=attestation.attested_at,
            host_isolation_verified=attestation.host_isolation_verified,
            last_heartbeat_at=now,
        )
        token.status = "used"
        token.used_at = now
        self._session.add_all([credential, worker])
        self._events.append_runtime_event(runtime, "self_hosted.registered", data.machine_id)
        self._events.append_runtime_space_event(
            runtime,
            "self_hosted.registered",
            data.machine_id,
        )
        self._record_attestation(runtime, attestation, event="registered")
        self._session.commit()
        self._session.refresh(runtime)
        self._session.refresh(worker)
        return RegisteredRuntime(
            workspace_id=token.workspace_id,
            workspace_runtime_id=runtime.id,
            worker_id=worker.id,
            credential_token=credential_token,
            capability_attestation_state=worker.capability_attestation_state,
            host_isolation_verified=worker.host_isolation_verified,
        )

    def authenticate_worker(self, credential_token: str) -> AuthenticatedWorker:
        credential = self._session.scalar(
            select(RuntimeCredential).where(
                RuntimeCredential.token_hash == self.hash_token(credential_token),
                RuntimeCredential.status == "active",
            )
        )
        if credential is None:
            raise ValueError("Invalid runtime credential")
        runtime = self._session.get(WorkspaceRuntime, credential.workspace_runtime_id)
        worker = self._session.scalar(
            select(SelfHostedWorker).where(
                SelfHostedWorker.workspace_runtime_id == credential.workspace_runtime_id
            )
        )
        if runtime is None or worker is None:
            raise ValueError("Runtime credential is orphaned")
        if (
            runtime.workspace_id != credential.workspace_id
            or worker.workspace_id != credential.workspace_id
        ):
            raise ValueError("Runtime credential workspace binding is invalid")
        if runtime.status in {"revoked", "disabled"} or worker.status in {"revoked", "disabled"}:
            raise ValueError("Self-hosted runtime credential is revoked")
        credential.last_used_at = datetime.now(UTC)
        return AuthenticatedWorker(worker=worker, runtime=runtime, credential=credential)

    def heartbeat(
        self,
        auth: AuthenticatedWorker,
        data: WorkerHeartbeatRequest,
    ) -> SelfHostedWorker:
        now = datetime.now(UTC)
        if auth.worker.status in {"revoked", "disabled"} or auth.runtime.status in {
            "revoked",
            "disabled",
        }:
            raise ValueError("Revoked self-hosted workers cannot send heartbeats")
        if auth.worker.status == "quarantined" or auth.runtime.status == "quarantined":
            raise ValueError("Self-hosted worker is quarantined")
        if data.status not in {"online", "offline", "degraded"}:
            raise ValueError("Unsupported self-hosted heartbeat status")
        next_capabilities = data.capabilities or auth.worker.capabilities
        capabilities = self.validated_capabilities(
            auth.worker.workspace_id,
            next_capabilities,
            bound_runtime_space_id=auth.runtime.runtime_space_id,
        )
        attestation = self._heartbeat_attestation(auth, data, capabilities)
        auth.worker.status = data.status
        auth.worker.capabilities = capabilities
        auth.worker.capability_attestation_state = attestation.state
        auth.worker.capability_attestation_fingerprint = attestation.fingerprint
        auth.worker.capability_attestation_metadata = attestation.metadata
        auth.worker.capability_attested_at = attestation.attested_at
        auth.worker.host_isolation_verified = attestation.host_isolation_verified
        auth.worker.last_heartbeat_at = now
        auth.runtime.connection_status = "online" if data.status == "online" else data.status
        auth.runtime.capabilities = capabilities
        auth.runtime.last_heartbeat_at = now
        self._events.append_runtime_event(auth.runtime, "self_hosted.heartbeat", data.status)
        self._events.append_runtime_space_event(
            auth.runtime,
            "self_hosted.heartbeat",
            data.status,
        )
        if data.attestation is not None or data.capabilities:
            self._record_attestation(auth.runtime, attestation, event="heartbeat")
        self._session.commit()
        self._session.refresh(auth.worker)
        return auth.worker

    def _heartbeat_attestation(
        self,
        auth: AuthenticatedWorker,
        data: WorkerHeartbeatRequest,
        capabilities: dict[str, object],
    ) -> CapabilityAttestationResult:
        if data.attestation is None and not data.capabilities:
            return CapabilityAttestationResult(
                state=auth.worker.capability_attestation_state or "untrusted",
                fingerprint=auth.worker.capability_attestation_fingerprint,
                metadata=dict(auth.worker.capability_attestation_metadata or {}),
                host_isolation_verified=auth.worker.host_isolation_verified,
                attested_at=auth.worker.capability_attested_at,
            )
        return evaluate_capability_attestation(
            workspace_id=auth.worker.workspace_id,
            machine_id=auth.worker.machine_id,
            capabilities=capabilities,
            attestation=data.attestation,
            settings=self._settings,
        )

    def _record_attestation(
        self,
        runtime: WorkspaceRuntime,
        attestation: CapabilityAttestationResult,
        *,
        event: str,
    ) -> None:
        metadata = {
            "state": attestation.state,
            "fingerprint": attestation.fingerprint,
            "host_isolation_verified": attestation.host_isolation_verified,
            "attested_at": (
                attestation.attested_at.isoformat() if attestation.attested_at is not None else None
            ),
            "reason": attestation.metadata.get("reason"),
        }
        self._events.append_runtime_event(
            runtime,
            f"self_hosted.attestation_{event}",
            attestation.state,
            metadata,
        )
        if attestation.state != "verified":
            self._events.append_security_event(
                workspace_id=runtime.workspace_id,
                action="self_hosted.attestation.untrusted",
                outcome="untrusted",
                severity="warning",
                reason=str(attestation.metadata.get("reason") or "untrusted_attestation"),
                metadata={"runtime_id": str(runtime.id), **metadata},
            )

    def registration_runtime_space_id(
        self,
        workspace_id: UUID,
        capabilities: dict[str, object],
    ) -> UUID | None:
        runtime_space_id = uuid_from_capabilities(capabilities, "runtime_space_id")
        if runtime_space_id is None:
            return None
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if (
            runtime_space is None
            or runtime_space.workspace_id != workspace_id
            or runtime_space.status != "active"
        ):
            raise ValueError("Runtime space not found")
        return runtime_space_id

    def validated_capabilities(
        self,
        workspace_id: UUID,
        capabilities: dict[str, object],
        *,
        bound_runtime_space_id: UUID | None = None,
    ) -> dict[str, object]:
        normalized = dict(capabilities)
        referenced_ids, invalid_values = capability_runtime_space_references(normalized)
        if invalid_values:
            raise ValueError(
                "Self-hosted capabilities include invalid runtime space IDs: "
                + ", ".join(invalid_values)
            )
        runtime_space_id = uuid_from_capabilities(normalized, "runtime_space_id")
        if (
            runtime_space_id is not None
            and bound_runtime_space_id is not None
            and runtime_space_id != bound_runtime_space_id
        ):
            raise ValueError("Self-hosted runtime space binding cannot be changed by heartbeat")
        if bound_runtime_space_id is not None:
            referenced_ids.discard(bound_runtime_space_id)
        if not referenced_ids:
            return normalized
        active_space_ids = {
            runtime_space_id
            for runtime_space_id in self._session.scalars(
                select(RuntimeSpace.id).where(
                    RuntimeSpace.workspace_id == workspace_id,
                    RuntimeSpace.id.in_(referenced_ids),
                    RuntimeSpace.status == "active",
                )
            ).all()
        }
        invalid_ids = sorted(
            str(runtime_space_id) for runtime_space_id in referenced_ids - active_space_ids
        )
        if invalid_ids:
            raise ValueError(
                "Self-hosted capabilities reference unavailable runtime spaces: "
                + ", ".join(invalid_ids)
            )
        return normalized

    def hash_token(self, token: str) -> str:
        material = f"{self._settings.token_hash_pepper}:{token}"
        return sha256(material.encode("utf-8")).hexdigest()

    def _consume_enrollment_token(self, raw_token: str) -> RuntimeEnrollmentToken:
        token = self._session.scalar(
            select(RuntimeEnrollmentToken).where(
                RuntimeEnrollmentToken.token_hash == self.hash_token(raw_token),
                RuntimeEnrollmentToken.status == "active",
            )
        )
        if token is None:
            raise ValueError("Invalid enrollment token")
        if token.expires_at is not None and token.expires_at < datetime.now(UTC):
            raise ValueError("Enrollment token expired")
        return token


def uuid_from_capabilities(capabilities: dict[str, object], key: str) -> UUID | None:
    value = capabilities.get(key)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def capability_runtime_space_references(
    capabilities: dict[str, object],
) -> tuple[set[UUID], list[str]]:
    ids: set[UUID] = set()
    invalid_values: list[str] = []
    raw_runtime_space_id = capabilities.get("runtime_space_id")
    if raw_runtime_space_id is not None:
        if not isinstance(raw_runtime_space_id, str):
            invalid_values.append(str(raw_runtime_space_id))
        else:
            try:
                ids.add(UUID(raw_runtime_space_id))
            except ValueError:
                invalid_values.append(raw_runtime_space_id)
    raw_allowed_ids = capabilities.get("allowed_runtime_space_ids")
    if raw_allowed_ids is None:
        return ids, invalid_values
    if not isinstance(raw_allowed_ids, list):
        invalid_values.append(str(raw_allowed_ids))
        return ids, invalid_values
    for raw_id in raw_allowed_ids:
        if not isinstance(raw_id, str):
            invalid_values.append(str(raw_id))
            continue
        try:
            ids.add(UUID(raw_id))
        except ValueError:
            invalid_values.append(raw_id)
    return ids, invalid_values
