from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.observability.audit_service import AuditService
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCreateRequest,
    RuntimeExecutionMode,
    RuntimeLimits,
    RuntimeMount,
    validate_runtime_execution_mode,
)
from backend.app.runtime_manager.events import RuntimeEventLog
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.metadata import (
    default_runtime_hardening_policy,
    runtime_isolation_metadata,
    runtime_labels,
)
from backend.app.runtime_manager.models import RuntimeLease, RuntimeTemplate, WorkspaceRuntime
from backend.app.runtime_manager.pool_leases import RuntimeLeaseStore

MANAGED_RUNTIME_PROVIDERS = frozenset({"docker", "cloud_docker"})
_RUNTIME_WORKSPACE_ROOT = "/workspace"


class RuntimeEnvironmentError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RunRuntimeEnvironmentResult:
    runtime: WorkspaceRuntime | None
    created: bool


class RunRuntimeEnvironmentService:
    """Bind a run to an isolated, pooled, or persistent managed runtime.

    A workspace runtime is a durable placement and policy anchor. Isolated mode creates a fresh
    child container and volume. Pooled mode leases a pre-provisioned container and gives the run
    a logical child binding with a private ``/workspace/runs/<run_id>`` tree. Persistent mode binds
    the run directly to its dedicated runtime and intentionally retains its project tree.
    """

    def __init__(self, session: Session, docker_client: DockerRuntimeClient | None) -> None:
        self._session = session
        self._docker = docker_client

    def ensure_for_run(self, run: AgentRun) -> RunRuntimeEnvironmentResult:
        parent = self._parent_for_run(run)
        if parent is None:
            return RunRuntimeEnvironmentResult(runtime=None, created=False)
        mode = _execution_mode(parent)
        if mode == "none":
            run.execution_runtime_id = None
            _set_run_execution_metadata(
                run,
                status="not_required",
                runtime_id=parent.id,
                mode="none",
            )
            self._session.flush([run])
            return RunRuntimeEnvironmentResult(runtime=None, created=False)
        self._require_managed_parent(parent)
        if mode == "pooled":
            return self._ensure_pooled_for_run(run, parent)
        if mode == "persistent":
            return self._ensure_persistent_for_run(run, parent)
        return self._ensure_isolated_for_run(run, parent)

    def _ensure_isolated_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> RunRuntimeEnvironmentResult:
        docker = self._docker
        if docker is None:
            raise RuntimeEnvironmentError(
                "runtime_execution_client_missing",
                "Managed run execution requires a worker-injected Docker client",
            )
        existing = self._existing_for_run(run, parent)
        if existing is not None:
            self._require_parent_binding(existing, parent, run)
            if existing.status in {"active", "running"} and existing.connection_status == "online":
                run.execution_runtime_id = existing.id
                return RunRuntimeEnvironmentResult(runtime=existing, created=False)
            if existing.status in {"created", "provisioning"} and existing.docker_container_id:
                docker = self._docker
                if docker is None:
                    raise RuntimeEnvironmentError(
                        "runtime_execution_client_missing",
                        "Managed run execution requires a worker-injected Docker client",
                    )
                docker.start_container(existing.docker_container_id)
                existing.status = "active"
                existing.connection_status = "online"
                existing.last_heartbeat_at = datetime.now(UTC)
                run.execution_runtime_id = existing.id
                self._session.flush([existing, run])
                return RunRuntimeEnvironmentResult(runtime=existing, created=False)
            raise RuntimeEnvironmentError(
                "runtime_execution_unavailable",
                "The run execution runtime is not active and online",
            )

        template = self._template(parent)
        limits = _runtime_limits(parent.limits)
        network_policy = dict(parent.network_policy or {})
        network_disabled = _network_disabled(network_policy)
        hardening = default_runtime_hardening_policy()
        volume_name = _run_volume_name(run.workspace_id, run.id)
        child = WorkspaceRuntime(
            workspace_id=run.workspace_id,
            runtime_template_id=parent.runtime_template_id,
            runtime_space_id=parent.runtime_space_id,
            runtime_provider=parent.runtime_provider,
            runtime_type=parent.runtime_type,
            execution_mode="isolated",
            pool_key=None,
            name=f"run-{run.id}",
            status="provisioning",
            connection_status="offline",
            parent_runtime_id=parent.id,
            execution_run_id=run.id,
            limits=dict(parent.limits or {}),
            network_policy=network_policy,
            capabilities={},
        )
        self._session.add(child)
        self._session.flush([child])
        isolation = dict(
            runtime_isolation_metadata(
                workspace_id=run.workspace_id,
                runtime_id=child.id,
                runtime_space_id=parent.runtime_space_id,
                network_disabled=network_disabled,
                network_policy=network_policy,
            )
        )
        isolation["execution"] = {
            "mode": "isolated",
            "run_id": str(run.id),
            "parent_runtime_id": str(parent.id),
            "ephemeral": True,
        }
        persistent_mounts = _persistent_mounts(parent.capabilities)
        child.capabilities = {
            "isolation": isolation,
            "hardening": _object_dict(parent.capabilities.get("hardening")),
            "execution": {
                "mode": "isolated",
                "run_id": str(run.id),
                "parent_runtime_id": str(parent.id),
                "cleanup_status": "pending",
                "persistent_mounts": [
                    {
                        "source": mount.source,
                        "target": mount.target,
                        "type": mount.mount_type,
                        "read_only": mount.read_only,
                    }
                    for mount in persistent_mounts
                ],
            },
            "managed_resources": {"docker_volumes": [volume_name]},
        }
        container_id: str | None = None
        try:
            container_id = docker.create_container(
                RuntimeCreateRequest(
                    image=template.image,
                    name=f"opsmesh-run-{run.workspace_id.hex[:12]}-{run.id.hex}",
                    workspace_id=str(run.workspace_id),
                    runtime_id=str(child.id),
                    runtime_space_id=(
                        str(parent.runtime_space_id)
                        if parent.runtime_space_id is not None
                        else None
                    ),
                    limits=limits,
                    network_disabled=network_disabled,
                    network_policy=network_policy,
                    labels={
                        **runtime_labels(child),
                        "opsmesh.run_id": str(run.id),
                        "opsmesh.parent_runtime_id": str(parent.id),
                    },
                    mounts=(
                        RuntimeMount(source=volume_name, target=_RUNTIME_WORKSPACE_ROOT),
                        *persistent_mounts,
                    ),
                    hardening=hardening,
                    working_dir=_RUNTIME_WORKSPACE_ROOT,
                )
            )
            docker.start_container(container_id)
        except Exception as exc:
            self._discard_failed_child(child, container_id, volume_name)
            raise RuntimeEnvironmentError(
                "runtime_execution_provisioning_failed",
                "The per-run execution runtime could not be provisioned",
            ) from exc

        child.docker_container_id = container_id
        child.status = "active"
        child.connection_status = "online"
        child.last_heartbeat_at = datetime.now(UTC)
        RuntimeLeaseStore(self._session).ensure(
            child,
            status="active",
            metadata={
                "scope": "isolated_run",
                "run_id": str(run.id),
                "parent_runtime_id": str(parent.id),
                "isolation": isolation,
                "hardening": child.capabilities["hardening"],
            },
        )
        RuntimeEventLog(self._session).append(
            child,
            "runtime.run.created",
            "Ephemeral per-run execution runtime created",
            metadata={
                "run_id": str(run.id),
                "parent_runtime_id": str(parent.id),
                "isolation": isolation,
            },
        )
        RunEventRecorder(self._session).append_event(
            run,
            "run.runtime_environment.created",
            "Per-run isolated execution environment created",
            {
                "execution_runtime_id": str(child.id),
                "parent_runtime_id": str(parent.id),
                "persistent_mount_count": len(persistent_mounts),
            },
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="run.runtime_environment.created",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "execution_runtime_id": str(child.id),
                "parent_runtime_id": str(parent.id),
                "persistent_mount_count": len(persistent_mounts),
            },
        )
        run.execution_runtime_id = child.id
        _set_run_execution_metadata(run, status="active", runtime_id=child.id)
        self._session.flush([child, run])
        return RunRuntimeEnvironmentResult(runtime=child, created=True)

    def _ensure_persistent_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> RunRuntimeEnvironmentResult:
        was_active = (
            _runtime_execution_mode(run) == "persistent"
            and _runtime_execution_status(run) == "active"
        )
        lease = RuntimeLeaseStore(self._session).acquire_pool_member(
            parent,
            run_id=run.id,
            metadata={
                "mode": "persistent",
                "runtime_id": str(parent.id),
                "parent_runtime_id": str(parent.id),
            },
        )
        if lease is None:
            raise RuntimeEnvironmentError(
                "runtime_persistent_busy",
                "The persistent runtime is already attached to another run",
            )
        parent.last_heartbeat_at = datetime.now(UTC)
        run.execution_runtime_id = None
        _set_run_execution_metadata(
            run,
            status="active",
            runtime_id=parent.id,
            mode="persistent",
        )
        if not was_active:
            RuntimeEventLog(self._session).append(
                parent,
                "runtime.run.persistent_attached",
                "Persistent runtime attached to agent run",
                metadata={"run_id": str(run.id)},
            )
            RunEventRecorder(self._session).append_event(
                run,
                "run.runtime_environment.attached",
                "Persistent runtime attached to run",
                {"runtime_id": str(parent.id), "mode": "persistent"},
            )
            AuditService(self._session).record_system_action(
                workspace_id=run.workspace_id,
                action="run.runtime_environment.attached",
                target_type="agent_run",
                target_id=run.id,
                metadata={"runtime_id": str(parent.id), "mode": "persistent"},
            )
        self._session.flush([parent, run])
        return RunRuntimeEnvironmentResult(runtime=parent, created=not was_active)

    def _ensure_pooled_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> RunRuntimeEnvironmentResult:
        existing = self._existing_for_run(run, parent)
        if existing is not None:
            self._require_parent_binding(existing, parent, run)
            if existing.execution_mode != "pooled":
                raise RuntimeEnvironmentError(
                    "runtime_execution_mode_mismatch",
                    "The run is bound to a runtime with a different execution mode",
                )
            if existing.status in {"active", "running"} and existing.connection_status == "online":
                run.execution_runtime_id = existing.id
                return RunRuntimeEnvironmentResult(runtime=existing, created=False)
            raise RuntimeEnvironmentError(
                "runtime_execution_unavailable",
                "The pooled execution runtime is not active and online",
            )

        member, _ = self._acquire_pool_member(parent, run)
        if member is None:
            raise RuntimeEnvironmentError(
                "runtime_pool_exhausted",
                "No available pooled runtime container matches the run policy",
            )
        now = datetime.now(UTC)
        isolation = _pooled_isolation_metadata(parent, member, run)
        child = WorkspaceRuntime(
            workspace_id=run.workspace_id,
            runtime_template_id=member.runtime_template_id,
            runtime_space_id=member.runtime_space_id,
            runtime_provider=member.runtime_provider,
            runtime_type=member.runtime_type,
            execution_mode="pooled",
            pool_key=member.pool_key,
            name=f"run-{run.id}",
            status="active",
            connection_status="online",
            parent_runtime_id=parent.id,
            execution_run_id=run.id,
            execution_pool_member_id=member.id,
            docker_container_id=member.docker_container_id,
            limits=dict(member.limits or {}),
            network_policy=dict(member.network_policy or {}),
            capabilities={
                "isolation": isolation,
                "hardening": _object_dict(member.capabilities.get("hardening")),
                "execution": {
                    "mode": "pooled",
                    "run_id": str(run.id),
                    "parent_runtime_id": str(parent.id),
                    "pool_member_runtime_id": str(member.id),
                    "cleanup_status": "pending",
                },
                "managed_resources": {"docker_volumes": []},
            },
            last_heartbeat_at=now,
        )
        self._session.add(child)
        self._session.flush([child])
        RuntimeLeaseStore(self._session).ensure(
            child,
            status="active",
            metadata={
                "scope": "pooled_run",
                "mode": "pooled",
                "run_id": str(run.id),
                "parent_runtime_id": str(parent.id),
                "pool_member_runtime_id": str(member.id),
                "container_reused": True,
            },
        )
        RuntimeEventLog(self._session).append(
            child,
            "runtime.run.acquired",
            "Pre-provisioned pooled runtime container leased to run",
            metadata={
                "run_id": str(run.id),
                "parent_runtime_id": str(parent.id),
                "pool_member_runtime_id": str(member.id),
            },
        )
        RunEventRecorder(self._session).append_event(
            run,
            "run.runtime_environment.acquired",
            "Pooled runtime container leased to run",
            {
                "execution_runtime_id": str(child.id),
                "parent_runtime_id": str(parent.id),
                "pool_member_runtime_id": str(member.id),
                "mode": "pooled",
            },
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="run.runtime_environment.acquired",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "execution_runtime_id": str(child.id),
                "parent_runtime_id": str(parent.id),
                "pool_member_runtime_id": str(member.id),
                "mode": "pooled",
            },
        )
        run.execution_runtime_id = child.id
        _set_run_execution_metadata(
            run,
            status="active",
            runtime_id=child.id,
            mode="pooled",
            pool_member_runtime_id=member.id,
        )
        self._session.flush([child, run])
        return RunRuntimeEnvironmentResult(runtime=child, created=True)

    def _acquire_pool_member(
        self,
        parent: WorkspaceRuntime,
        run: AgentRun,
    ) -> tuple[WorkspaceRuntime | None, RuntimeLease | None]:
        statement = (
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == parent.workspace_id,
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.runtime_provider.in_(MANAGED_RUNTIME_PROVIDERS),
                WorkspaceRuntime.status.in_(["active", "running"]),
                WorkspaceRuntime.connection_status == "online",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.execution_pool_member_id.is_(None),
                WorkspaceRuntime.runtime_template_id == parent.runtime_template_id,
                WorkspaceRuntime.runtime_space_id == parent.runtime_space_id,
            )
            .order_by(WorkspaceRuntime.updated_at.asc(), WorkspaceRuntime.created_at.asc())
            .with_for_update(skip_locked=True)
        )
        for member in self._session.scalars(statement):
            if _pool_key(member) != _pool_key(parent) or not _pool_policy_matches(parent, member):
                continue
            if not member.docker_container_id:
                continue
            lease = RuntimeLeaseStore(self._session).acquire_pool_member(
                member,
                run_id=run.id,
                metadata={
                    "mode": "pooled",
                    "parent_runtime_id": str(parent.id),
                    "pool_key": _pool_key(parent),
                },
            )
            if lease is not None:
                return member, lease
        return None, None

    def _parent_for_run(self, run: AgentRun) -> WorkspaceRuntime | None:
        if run.runtime_id is None:
            return None
        return self._runtime(run.workspace_id, run.runtime_id)

    def _require_managed_parent(self, parent: WorkspaceRuntime) -> None:
        if parent.runtime_provider not in MANAGED_RUNTIME_PROVIDERS:
            raise RuntimeEnvironmentError(
                "runtime_provider_unsupported",
                "The selected runtime provider does not expose managed execution",
            )
        if not isinstance(parent.capabilities.get("isolation"), dict):
            raise RuntimeEnvironmentError(
                "runtime_isolation_unverified",
                "Managed runtime has no platform isolation evidence",
            )
        if self._docker is None:
            raise RuntimeEnvironmentError(
                "runtime_execution_client_missing",
                "Managed run execution requires a worker-injected Docker client",
            )

    def cleanup_for_run(self, run: AgentRun) -> bool:
        execution_runtime_id = run.execution_runtime_id
        if execution_runtime_id is None:
            if run.runtime_id is not None:
                parent = self._runtime(run.workspace_id, run.runtime_id)
                if _execution_mode(parent) == "persistent":
                    return self._release_persistent_for_run(run, parent)
            return True
        child = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.id == execution_runtime_id,
                WorkspaceRuntime.execution_run_id == run.id,
            )
        )
        if child is None:
            _set_run_execution_metadata(run, status="not_required", runtime_id=execution_runtime_id)
            self._record_cleanup(run, status="not_required", runtime_id=execution_runtime_id)
            return True
        if child.status == "deleted":
            if child.execution_mode == "pooled":
                self._release_deleted_pooled_member(run, child)
            _set_run_execution_metadata(run, status="completed", runtime_id=child.id)
            return True
        if self._docker is None:
            _set_run_execution_metadata(run, status="failed", runtime_id=child.id)
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return False
        if child.execution_mode == "pooled":
            return self._release_pooled_for_run(run, child)
        try:
            RuntimeManager(self._session, self._docker).delete_runtime(child)
        except Exception:
            _set_run_execution_metadata(run, status="failed", runtime_id=child.id)
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return False
        _set_run_execution_metadata(run, status="completed", runtime_id=child.id)
        self._record_cleanup(run, status="completed", runtime_id=child.id)
        return True

    def _release_deleted_pooled_member(
        self,
        run: AgentRun,
        child: WorkspaceRuntime,
    ) -> None:
        member_id = child.execution_pool_member_id
        if member_id is None:
            return
        member = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.id == member_id,
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.execution_pool_member_id.is_(None),
            )
        )
        if member is not None:
            RuntimeLeaseStore(self._session).release_pool_member(member, run_id=run.id)

    def reclaim_orphaned_pool_leases(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 600,
        limit: int = 100,
    ) -> tuple[int, int]:
        """Reclaim terminal or abandoned pooled leases during worker maintenance."""
        cutoff = datetime.now(UTC).timestamp() - stale_after_seconds
        statement = (
            select(RuntimeLease)
            .join(
                WorkspaceRuntime,
                (WorkspaceRuntime.workspace_id == RuntimeLease.workspace_id)
                & (WorkspaceRuntime.id == RuntimeLease.workspace_runtime_id),
            )
            .where(
                RuntimeLease.status == "leased",
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.execution_pool_member_id.is_(None),
            )
            .order_by(RuntimeLease.updated_at.asc())
            .limit(limit)
        )
        if workspace_id is not None:
            statement = statement.where(RuntimeLease.workspace_id == workspace_id)
        reclaimed = 0
        failed = 0
        for lease in self._session.scalars(statement).all():
            member = self._session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == lease.workspace_id,
                    WorkspaceRuntime.id == lease.workspace_runtime_id,
                )
            )
            if member is None:
                continue
            run_id = _lease_pool_run_id(lease)
            run = (
                self._session.scalar(
                    select(AgentRun).where(
                        AgentRun.workspace_id == lease.workspace_id,
                        AgentRun.id == run_id,
                    )
                )
                if run_id is not None
                else None
            )
            if run is not None and _run_is_terminal(run):
                if self.cleanup_for_run(run):
                    reclaimed += 1
                else:
                    failed += 1
                continue
            lease_age = lease.updated_at.timestamp() if lease.updated_at is not None else 0
            if run is not None and lease_age >= cutoff:
                continue
            child = (
                self._session.scalar(
                    select(WorkspaceRuntime).where(
                        WorkspaceRuntime.workspace_id == lease.workspace_id,
                        WorkspaceRuntime.execution_mode == "pooled",
                        WorkspaceRuntime.execution_run_id == run_id,
                        WorkspaceRuntime.execution_pool_member_id == member.id,
                        WorkspaceRuntime.status != "deleted",
                    )
                )
                if run_id is not None
                else None
            )
            if self._destroy_orphaned_pool_member(member, child, run):
                reclaimed += 1
            else:
                failed += 1
        self._session.flush()
        return reclaimed, failed

    def _destroy_orphaned_pool_member(
        self,
        member: WorkspaceRuntime,
        child: WorkspaceRuntime | None,
        run: AgentRun | None,
    ) -> bool:
        docker = self._docker
        if docker is None:
            return False
        try:
            RuntimeManager(self._session, docker).delete_runtime(
                member,
                allow_active_pool_lease=True,
            )
        except Exception:
            member.status = "cleanup_failed"
            member.connection_status = "offline"
            return False
        if child is not None:
            child.status = "deleted"
            child.connection_status = "offline"
            RuntimeLeaseStore(self._session).set_status(
                child,
                "released",
                released_at=datetime.now(UTC),
            )
        if run is not None:
            runtime_id = child.id if child is not None else member.id
            _set_run_execution_metadata(
                run,
                status="failed",
                runtime_id=runtime_id,
                mode="pooled",
                pool_member_runtime_id=member.id,
            )
            self._record_cleanup(run, status="failed", runtime_id=runtime_id)
        return True

    def _release_persistent_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> bool:
        try:
            RuntimeLeaseStore(self._session).release_pool_member(parent, run_id=run.id)
        except Exception:
            _set_run_execution_metadata(
                run,
                status="failed",
                runtime_id=parent.id,
                mode="persistent",
            )
            self._record_cleanup(run, status="failed", runtime_id=parent.id)
            return False
        _set_run_execution_metadata(
            run,
            status="completed",
            runtime_id=parent.id,
            mode="persistent",
        )
        self._record_cleanup(run, status="completed", runtime_id=parent.id)
        return True

    def _release_pooled_for_run(self, run: AgentRun, child: WorkspaceRuntime) -> bool:
        member_id = child.execution_pool_member_id
        if member_id is None:
            _set_run_execution_metadata(
                run,
                status="failed",
                runtime_id=child.id,
                mode="pooled",
            )
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return False
        member = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.id == member_id,
                WorkspaceRuntime.execution_mode == "pooled",
                WorkspaceRuntime.execution_run_id.is_(None),
                WorkspaceRuntime.execution_pool_member_id.is_(None),
            )
        )
        if member is None or not member.docker_container_id or self._docker is None:
            _set_run_execution_metadata(run, status="failed", runtime_id=child.id, mode="pooled")
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return False
        try:
            self._reset_pooled_member(member, run)
        except Exception as exc:
            self._destroy_failed_pool_member(member, child, run, exc)
            return False
        now = datetime.now(UTC)
        child.status = "deleted"
        child.connection_status = "offline"
        child.last_heartbeat_at = now
        RuntimeLeaseStore(self._session).set_status(
            child,
            "released",
            released_at=now,
        )
        RuntimeLeaseStore(self._session).release_pool_member(member, run_id=run.id)
        RuntimeEventLog(self._session).append(
            child,
            "runtime.run.released",
            "Pooled runtime container returned to the pool",
            metadata={"run_id": str(run.id), "pool_member_runtime_id": str(member.id)},
        )
        self._session.flush([child, member])
        _set_run_execution_metadata(
            run,
            status="completed",
            runtime_id=child.id,
            mode="pooled",
            pool_member_runtime_id=member.id,
        )
        self._record_cleanup(run, status="completed", runtime_id=child.id)
        return True

    def _reset_pooled_member(self, member: WorkspaceRuntime, run: AgentRun) -> None:
        docker = self._docker
        if docker is None or member.docker_container_id is None:
            raise RuntimeError("Pooled runtime container is unavailable")
        timeout_seconds = _runtime_timeout(member.limits)
        process_cleanup = docker.exec_command(
            member.docker_container_id,
            [
                "sh",
                "-c",
                (
                    "for proc in /proc/[0-9]*; do "
                    'pid="${proc##*/}"; '
                    '[ "$pid" -gt 1 ] && [ "$pid" -ne "$$" ] && '
                    'kill -KILL "$pid" 2>/dev/null || true; '
                    "done"
                ),
            ],
            timeout_seconds,
            working_dir="/",
        )
        if process_cleanup.exit_code != 0:
            raise RuntimeError("Pooled runtime process cleanup failed")
        workspace_cleanup = docker.exec_command(
            member.docker_container_id,
            ["rm", "-rf", "--", f"{_RUNTIME_WORKSPACE_ROOT}/runs/{run.id}"],
            timeout_seconds,
            working_dir="/",
        )
        if workspace_cleanup.exit_code != 0:
            raise RuntimeError("Pooled runtime workspace cleanup failed")

    def _destroy_failed_pool_member(
        self,
        member: WorkspaceRuntime,
        child: WorkspaceRuntime,
        run: AgentRun,
        error: Exception,
    ) -> None:
        docker = self._docker
        if docker is None:
            member.status = "cleanup_failed"
            member.connection_status = "offline"
            child.status = "cleanup_failed"
            child.connection_status = "offline"
            _set_run_execution_metadata(
                run,
                status="failed",
                runtime_id=child.id,
                mode="pooled",
                pool_member_runtime_id=member.id,
            )
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return
        try:
            RuntimeManager(self._session, docker).delete_runtime(
                member,
                allow_active_pool_lease=True,
            )
        except Exception:
            member.status = "cleanup_failed"
            member.connection_status = "offline"
        child.status = "deleted" if member.status == "deleted" else "cleanup_failed"
        child.connection_status = "offline"
        RuntimeLeaseStore(self._session).set_status(
            child,
            "released" if child.status == "deleted" else "cleanup_failed",
            released_at=datetime.now(UTC),
        )
        _set_run_execution_metadata(
            run,
            status="completed" if child.status == "deleted" else "failed",
            runtime_id=child.id,
            mode="pooled",
            pool_member_runtime_id=member.id,
        )
        self._record_cleanup(
            run,
            status="completed" if child.status == "deleted" else "failed",
            runtime_id=child.id,
        )

    def _record_cleanup(self, run: AgentRun, *, status: str, runtime_id: UUID) -> None:
        mode = _runtime_execution_mode(run) or "isolated"
        metadata: dict[str, object] = {
            "execution_runtime_id": str(runtime_id),
            "cleanup_status": status,
            "mode": mode,
        }
        if status in {"completed", "not_required"}:
            event_type = {
                "pooled": "run.runtime_environment.released",
                "persistent": "run.runtime_environment.persistent_released",
            }.get(mode, "run.runtime_environment.cleaned")
        else:
            event_type = "run.runtime_environment.cleanup_failed"
        existing = self._session.scalar(
            select(AgentRun.id).where(
                AgentRun.workspace_id == run.workspace_id,
                AgentRun.id == run.id,
            )
        )
        if existing is None:
            return
        _append_once = run.input.get("runtime_execution", {}) if isinstance(run.input, dict) else {}
        if isinstance(_append_once, dict) and _append_once.get("cleanup_event") == event_type:
            return
        RunEventRecorder(self._session).append_event(
            run,
            event_type,
            "Runtime execution environment cleanup completed"
            if status in {"completed", "not_required"}
            else "Per-run execution runtime cleanup failed",
            metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action=event_type,
            target_type="agent_run",
            target_id=run.id,
            metadata=metadata,
        )
        run_input = dict(run.input or {})
        execution_value = run_input.get("runtime_execution")
        execution = dict(execution_value) if isinstance(execution_value, dict) else {}
        execution["cleanup_event"] = event_type
        run_input["runtime_execution"] = execution
        run.input = run_input
        self._session.flush([run])

    def _runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime:
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == runtime_id,
            )
        )
        if runtime is None:
            raise RuntimeEnvironmentError(
                "runtime_unavailable",
                "The authorized parent runtime was not found in the workspace",
            )
        return runtime

    def _existing_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> WorkspaceRuntime | None:
        if run.execution_runtime_id is not None:
            existing = self._session.scalar(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.workspace_id == run.workspace_id,
                    WorkspaceRuntime.id == run.execution_runtime_id,
                )
            )
            if existing is None:
                raise RuntimeEnvironmentError(
                    "runtime_execution_unavailable",
                    "The run execution runtime was not found in the workspace",
                )
            return existing
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == run.workspace_id,
                WorkspaceRuntime.parent_runtime_id == parent.id,
                WorkspaceRuntime.execution_run_id == run.id,
                WorkspaceRuntime.status != "deleted",
            )
        )

    def _template(self, runtime: WorkspaceRuntime) -> RuntimeTemplate:
        if runtime.runtime_template_id is None:
            raise RuntimeEnvironmentError(
                "runtime_template_unavailable",
                "The parent runtime has no execution image",
            )
        template = self._session.scalar(
            select(RuntimeTemplate).where(RuntimeTemplate.id == runtime.runtime_template_id)
        )
        if template is None or template.status != "active":
            raise RuntimeEnvironmentError(
                "runtime_template_unavailable",
                "The parent runtime execution image is unavailable",
            )
        return template

    @staticmethod
    def _require_parent_binding(
        child: WorkspaceRuntime,
        parent: WorkspaceRuntime,
        run: AgentRun,
    ) -> None:
        if child.parent_runtime_id != parent.id or child.execution_run_id != run.id:
            raise RuntimeEnvironmentError(
                "runtime_execution_binding_mismatch",
                "The per-run runtime is not bound to the authorized parent runtime",
            )

    def _discard_failed_child(
        self,
        child: WorkspaceRuntime,
        container_id: str | None,
        volume_name: str,
    ) -> None:
        if self._docker is not None and container_id is not None:
            with suppress(Exception):
                self._docker.remove_container(container_id)
        if self._docker is not None:
            with suppress(Exception):
                self._docker.remove_volume(volume_name)
        self._session.delete(child)
        self._session.flush()


def _runtime_limits(raw: dict[str, object]) -> RuntimeLimits:
    cpu_count = raw.get("cpu_count")
    if isinstance(cpu_count, bool) or not isinstance(cpu_count, int | float) or cpu_count <= 0:
        raise RuntimeEnvironmentError(
            "runtime_limits_invalid",
            "Parent runtime CPU limit is invalid",
        )
    memory_mb = _positive_runtime_limit(raw, "memory_mb")
    disk_mb = _positive_runtime_limit(raw, "disk_mb")
    timeout_seconds = _positive_runtime_limit(raw, "timeout_seconds")
    max_output_bytes = _positive_runtime_limit(raw, "max_output_bytes", default=256_000)
    max_processes = _positive_runtime_limit(raw, "max_processes", default=256)
    return RuntimeLimits(
        cpu_count=float(cpu_count),
        memory_mb=memory_mb,
        disk_mb=disk_mb,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        max_processes=max_processes,
    )


def _positive_runtime_limit(
    raw: dict[str, object],
    key: str,
    *,
    default: int | None = None,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeEnvironmentError(
            "runtime_limits_invalid",
            f"Parent runtime {key} limit is invalid",
        )
    return value


def _network_disabled(policy: dict[str, object]) -> bool:
    mode = policy.get("mode")
    return policy.get("disabled") is True or mode in {None, "none"}


def _persistent_mounts(capabilities: dict[str, object]) -> tuple[RuntimeMount, ...]:
    raw = capabilities.get("persistent_mounts")
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > 8:
        raise RuntimeEnvironmentError(
            "runtime_persistent_mounts_invalid",
            "Runtime persistent mounts must be a bounded list",
        )
    mounts: list[RuntimeMount] = []
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeEnvironmentError(
                "runtime_persistent_mounts_invalid",
                "Runtime persistent mount metadata is invalid",
            )
        source = item.get("source")
        target = item.get("target")
        mount_type = item.get("type", "volume")
        read_only = item.get("read_only", True)
        if (
            not isinstance(source, str)
            or not source
            or not isinstance(target, str)
            or not target.startswith("/")
            or target == _RUNTIME_WORKSPACE_ROOT
            or mount_type not in {"bind", "volume", "tmpfs", "npipe"}
            or not isinstance(read_only, bool)
        ):
            raise RuntimeEnvironmentError(
                "runtime_persistent_mounts_invalid",
                "Runtime persistent mount metadata is invalid",
            )
        mounts.append(
            RuntimeMount(
                source=source,
                target=target,
                mount_type=mount_type,
                read_only=read_only,
            )
        )
    return tuple(mounts)


def _run_volume_name(workspace_id: UUID, run_id: UUID) -> str:
    return f"opsmesh-ws-{workspace_id.hex}-run-{run_id.hex}"


def _execution_mode(runtime: WorkspaceRuntime) -> RuntimeExecutionMode:
    if runtime.execution_mode == "none":
        return "none"
    try:
        return validate_runtime_execution_mode(runtime.execution_mode, runtime.pool_key)
    except ValueError as exc:
        raise RuntimeEnvironmentError(
            "runtime_execution_mode_invalid",
            "Runtime execution mode is invalid",
        ) from exc


def _runtime_execution_mode(run: AgentRun) -> str | None:
    value = run.input.get("runtime_execution") if isinstance(run.input, dict) else None
    if not isinstance(value, dict):
        return None
    mode = value.get("mode")
    return mode if isinstance(mode, str) else None


def _runtime_execution_status(run: AgentRun) -> str | None:
    value = run.input.get("runtime_execution") if isinstance(run.input, dict) else None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    return status if isinstance(status, str) else None


def _lease_pool_run_id(lease: RuntimeLease) -> UUID | None:
    pool = lease.lease_metadata.get("pool") if isinstance(lease.lease_metadata, dict) else None
    value = pool.get("run_id") if isinstance(pool, dict) else None
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _run_is_terminal(run: AgentRun) -> bool:
    return run.status in {"completed", "failed", "cancelled"}


def _pool_key(runtime: WorkspaceRuntime) -> str:
    return runtime.pool_key or f"runtime:{runtime.id}"


def _pool_policy_matches(parent: WorkspaceRuntime, member: WorkspaceRuntime) -> bool:
    return (
        parent.runtime_provider == member.runtime_provider
        and parent.runtime_type == member.runtime_type
        and parent.runtime_template_id == member.runtime_template_id
        and parent.runtime_space_id == member.runtime_space_id
        and dict(parent.limits or {}) == dict(member.limits or {})
        and dict(parent.network_policy or {}) == dict(member.network_policy or {})
        and _isolation_policy(parent) == _isolation_policy(member)
    )


def _isolation_policy(runtime: WorkspaceRuntime) -> dict[str, object]:
    capabilities = runtime.capabilities or {}
    isolation = capabilities.get("isolation")
    mount_policy: dict[str, object] = {}
    network_policy: dict[str, object] = {}
    if isinstance(isolation, dict):
        mount = isolation.get("workspace_mount")
        if isinstance(mount, dict):
            mount_policy = {
                "type": mount.get("type"),
                "target": mount.get("target"),
                "mode": mount.get("mode"),
            }
        network = isolation.get("network")
        if isinstance(network, dict):
            network_policy = dict(network)
    return {
        "isolation": {
            "workspace_mount": mount_policy,
            "network": network_policy,
        },
        "hardening": _object_dict(capabilities.get("hardening")),
    }


def _pooled_isolation_metadata(
    parent: WorkspaceRuntime,
    member: WorkspaceRuntime,
    run: AgentRun,
) -> dict[str, object]:
    isolation = member.capabilities.get("isolation")
    if not isinstance(isolation, dict):
        raise RuntimeEnvironmentError(
            "runtime_isolation_unverified",
            "Pooled runtime member has no platform isolation evidence",
        )
    workspace_mount = isolation.get("workspace_mount")
    if not isinstance(workspace_mount, dict):
        raise RuntimeEnvironmentError(
            "runtime_isolation_unverified",
            "Pooled runtime member has no workspace mount evidence",
        )
    return {
        "workspace_id": str(run.workspace_id),
        "runtime_id": str(run.id),
        "runtime_space_id": (
            str(member.runtime_space_id) if member.runtime_space_id is not None else None
        ),
        "workspace_mount": dict(workspace_mount),
        "network": dict(isolation.get("network") or {}),
        "execution": {
            "mode": "pooled",
            "parent_runtime_id": str(parent.id),
            "pool_member_runtime_id": str(member.id),
            "run_id": str(run.id),
            "workspace_root": f"{_RUNTIME_WORKSPACE_ROOT}/runs/{run.id}",
        },
    }


def _runtime_timeout(limits: dict[str, object]) -> int:
    value = limits.get("timeout_seconds")
    if isinstance(value, int) and value > 0:
        return value
    raise RuntimeEnvironmentError(
        "runtime_limits_invalid",
        "Pooled runtime timeout limit is invalid",
    )


def _object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _set_run_execution_metadata(
    run: AgentRun,
    *,
    status: str,
    runtime_id: UUID,
    mode: RuntimeExecutionMode = "isolated",
    pool_member_runtime_id: UUID | None = None,
) -> None:
    run_input = dict(run.input or {})
    execution_value = run_input.get("runtime_execution")
    execution = dict(execution_value) if isinstance(execution_value, dict) else {}
    execution.update({"runtime_id": str(runtime_id), "status": status, "mode": mode})
    if pool_member_runtime_id is not None:
        execution["pool_member_runtime_id"] = str(pool_member_runtime_id)
    run_input["runtime_execution"] = execution
    run.input = run_input
