from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.runtime.instances.contracts import (
    DockerRuntimeClient,
    RuntimeCreateRequest,
    RuntimeHardeningPolicy,
    RuntimeLimits,
    RuntimeMount,
    RuntimeProcess,
)
from opsmesh.runtime.instances.events import RuntimeEventLog
from opsmesh.runtime.instances.hosts import RuntimeHostStore
from opsmesh.runtime.instances.leases import RuntimeLeaseStore, RuntimeSpaceReservationStore
from opsmesh.runtime.instances.metadata import (
    RuntimeIsolationMetadata,
    default_runtime_hardening_policy,
    runtime_hardening_metadata,
    runtime_isolation_metadata,
    runtime_labels,
    runtime_space_reservation_key,
    runtime_space_usage_for_runtime,
)
from opsmesh.runtime.instances.models import RuntimeTemplate, WorkspaceRuntime
from opsmesh.runtime.instances.policies.quotas import (
    RuntimeQuotaExceededError,
    RuntimeQuotaPolicy,
)
from opsmesh.runtime.instances.security_events import RuntimeSecurityEventRecorder
from opsmesh.runtime.spaces.reservations import RuntimeSpaceCapacityReservationService


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
        process: RuntimeProcess | None = None,
    ) -> WorkspaceRuntime:
        workspace_id = runtime.workspace_id
        runtime_space_id = runtime.runtime_space_id
        RuntimeQuotaPolicy(self._session).assert_can_create_runtime(
            workspace_id, limits, excluding_runtime_id=runtime.id, check_totals=False
        )
        host, created = RuntimeHostStore(self._session, self._docker).acquire(
            runtime, template, limits
        )
        if created:
            RuntimeQuotaPolicy(self._session).assert_can_create_runtime(
                workspace_id, limits, excluding_runtime_id=runtime.id
            )
        network_policy = dict(runtime.network_policy)
        isolation_metadata = runtime_isolation_metadata(
            workspace_id=workspace_id,
            runtime_id=host.id,
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
            "node_id": self._docker.node_identity(),
            "isolation": isolation_metadata,
            "hardening": hardening_metadata,
            "execution": {
                "mode": runtime.execution_mode,
                "shared_host": runtime.execution_mode == "shared",
                "network_enforcement": "nftables_socket_uid",
                "supervisor": {
                    "user": "0:0",
                    "capabilities": [
                        "NET_ADMIN",
                        "KILL",
                        "CHOWN",
                        "DAC_OVERRIDE",
                        "SETUID",
                        "SETGID",
                    ],
                },
                "workload": {
                    "user": "allocation_uid",
                    "capabilities": [],
                    "no_new_privileges": True,
                },
            },
            "policy_resolution": dict(policy_metadata or {}),
        }
        reservation_key = runtime_space_reservation_key(runtime)
        owns_provisioning = (
            host.status == "provisioning" and host.provisioning_owner_id == runtime.id
        )
        if not created and not owns_provisioning:
            runtime.status = host.status
            runtime.connection_status = "online" if host.status == "running" else "offline"
            self._session.commit()
            self._session.refresh(runtime)
            return runtime
        host.resources = {
            "docker_volumes": [isolation_metadata["workspace_mount"]["docker_volume"]],
        }
        runtime.status = "provisioning"
        try:
            self._reserve_runtime_space(
                runtime,
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                reservation_key=reservation_key,
                limits=limits,
            )
            # Freeze the request before ending the short reservation transaction.
            request = self._container_request(
                runtime,
                template=template,
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                limits=limits,
                network_disabled=network_disabled,
                network_policy=dict(runtime.network_policy),
                isolation_metadata=isolation_metadata,
                hardening_policy=hardening_policy,
                process=process,
            )
            self._session.commit()
            container_id = self._docker.create_container(request)
        except Exception:
            # Retain ownership and reservations when a Docker outcome is unknown.
            # Redelivery reconciles the deterministic container name instead of
            # provisioning a second host or discarding an unobserved container.
            self._session.rollback()
            raise

        host.docker_container_id = container_id
        host.status = "created"
        for binding in RuntimeHostStore(self._session, self._docker).bindings(runtime):
            binding.status = "created"
            binding.connection_status = "offline"
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
            "Managed runtime container created",
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
            "Managed runtime lease acquired",
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
        blocked_reason = reservation_result.blocked_reason or "runtime_space_quota_exceeded"
        raise RuntimeQuotaExceededError(
            blocked_reason,
            "Runtime space quota blocks Docker runtime creation",
        )

    def _container_request(
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
        process: RuntimeProcess | None,
    ) -> RuntimeCreateRequest:
        return RuntimeCreateRequest(
            image=template.image,
            name=f"opsmesh-{workspace_id}-{runtime.host_id}",
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
            process=process,
            shared_host=process is None,
        )
