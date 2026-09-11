from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCreateRequest,
    RuntimeHardeningPolicy,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime_manager.events import RuntimeEventLog
from backend.app.runtime_manager.metadata import (
    RuntimeIsolationMetadata,
    default_runtime_hardening_policy,
    runtime_hardening_metadata,
    runtime_isolation_metadata,
    runtime_labels,
    runtime_space_reservation_key,
    runtime_space_usage_for_runtime,
)
from backend.app.runtime_manager.pool.leases import RuntimeLeaseStore, RuntimeSpaceReservationStore
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError, RuntimeQuotaPolicy
from backend.app.runtime_manager.security_events import RuntimeSecurityEventRecorder
from backend.app.runtime_spaces.reservation_capacity import RuntimeSpaceCapacityReservationService
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime


class RuntimeProvisioningExecutor:
    def __init__(
        self,
        session: Session,
        docker_client: DockerRuntimeClient,
        events: RuntimeEventLog,
        leases: RuntimeLeaseStore,
        reservations: RuntimeSpaceReservationStore,
    ) -> None:
        self._session = session
        self._docker = docker_client
        self._events = events
        self._leases = leases
        self._reservations = reservations
        self._security_events = RuntimeSecurityEventRecorder(session)

    def provision_runtime(
        self,
        runtime: WorkspaceRuntime,
        *,
        template: RuntimeTemplate,
        limits: RuntimeLimits,
        network_disabled: bool,
        policy_metadata: dict[str, object] | None = None,
    ) -> WorkspaceRuntime:
        workspace_id = runtime.workspace_id
        runtime_space_id = runtime.runtime_space_id
        RuntimeQuotaPolicy(self._session).assert_can_create_runtime(workspace_id, limits)
        network_policy = dict(runtime.network_policy)
        isolation_metadata = runtime_isolation_metadata(
            workspace_id=workspace_id,
            runtime_id=runtime.id,
            runtime_space_id=runtime_space_id,
            network_disabled=network_disabled,
            network_policy=network_policy,
        )
        hardening_policy = default_runtime_hardening_policy()
        hardening_metadata = runtime_hardening_metadata(
            hardening_policy,
            isolation_metadata=isolation_metadata,
        )
        runtime.capabilities = {
            **dict(runtime.capabilities or {}),
            "isolation": isolation_metadata,
            "hardening": hardening_metadata,
            "execution": {
                "mode": runtime.execution_mode,
                "pool_key": runtime.pool_key,
                "pool_member": runtime.execution_mode == "pooled",
            },
            "policy_resolution": dict(policy_metadata or {}),
            "managed_resources": {
                "docker_volumes": [isolation_metadata["workspace_mount"]["docker_volume"]],
            },
        }
        reservation_key = runtime_space_reservation_key(runtime)
        self._reserve_runtime_space(
            runtime,
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            reservation_key=reservation_key,
            limits=limits,
        )

        try:
            container_id = self._create_container(
                runtime,
                template=template,
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                limits=limits,
                network_disabled=network_disabled,
                network_policy=dict(runtime.network_policy),
                isolation_metadata=isolation_metadata,
                hardening_policy=hardening_policy,
            )
        except Exception:
            self._reservations.release(runtime)
            self._session.delete(runtime)
            self._session.flush()
            raise

        runtime.docker_container_id = container_id
        runtime.status = "created"
        lease = self._leases.ensure(
            runtime,
            status="active",
            metadata={
                "action": "create",
                "image": template.image,
                "limits": dict(runtime.limits),
                "network_policy": dict(runtime.network_policy),
                "isolation": isolation_metadata,
                "hardening": hardening_metadata,
                "policy_resolution": dict(policy_metadata or {}),
                "runtime_space_reservation_key": reservation_key
                if runtime_space_id is not None
                else None,
            },
        )
        self._events.append(
            runtime,
            "runtime.created",
            container_id,
            metadata={
                "isolation": isolation_metadata,
                "network_policy": dict(runtime.network_policy),
                "hardening": hardening_metadata,
                "policy_resolution": dict(policy_metadata or {}),
            },
        )
        self._events.append(
            runtime,
            "runtime.lease_acquired",
            container_id,
            metadata={"runtime_lease_id": str(lease.id)},
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def _reserve_runtime_space(
        self,
        runtime: WorkspaceRuntime,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
        reservation_key: str,
        limits: RuntimeLimits,
    ) -> None:
        if runtime_space_id is None:
            return
        reservation_result = RuntimeSpaceCapacityReservationService(
            self._session
        ).reserve_run_capacity(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
            task_id=None,
            task_step_id=None,
            reservation_key=reservation_key,
            resource_usage=runtime_space_usage_for_runtime(limits),
        )
        if reservation_result.reservation is not None:
            return
        self._session.delete(runtime)
        self._session.flush()
        blocked_reason = reservation_result.blocked_reason or "runtime_space_quota_exceeded"
        raise RuntimeQuotaExceededError(
            blocked_reason,
            "Runtime space quota blocks Docker runtime creation",
        )

    def _create_container(
        self,
        runtime: WorkspaceRuntime,
        *,
        template: RuntimeTemplate,
        workspace_id: UUID,
        runtime_space_id: UUID | None,
        limits: RuntimeLimits,
        network_disabled: bool,
        network_policy: dict[str, object],
        isolation_metadata: RuntimeIsolationMetadata,
        hardening_policy: RuntimeHardeningPolicy,
    ) -> str:
        return self._docker.create_container(
            RuntimeCreateRequest(
                image=template.image,
                name=f"opsmesh-{workspace_id}-{runtime.id}",
                workspace_id=str(workspace_id),
                runtime_id=str(runtime.id),
                runtime_space_id=str(runtime_space_id) if runtime_space_id else None,
                limits=limits,
                network_disabled=network_disabled,
                network_policy=network_policy,
                labels=runtime_labels(runtime),
                mounts=(
                    RuntimeMount(
                        source=isolation_metadata["workspace_mount"]["docker_volume"],
                        target=isolation_metadata["workspace_mount"]["target"],
                    ),
                ),
                hardening=hardening_policy,
                working_dir=isolation_metadata["workspace_mount"]["target"],
            )
        )
