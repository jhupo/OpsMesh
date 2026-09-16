from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domains.orchestration.runs.events import RunEventRecorder
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.contracts import (
    RuntimeEnvironmentError,
    RuntimeExecutionMode,
    validate_runtime_execution_mode,
)
from backend.app.runtime.environment.contracts import (
    DockerRuntimeClient,
    RuntimeCreateRequest,
    RuntimeHardeningPolicy,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime.environment.events import RuntimeEventLog
from backend.app.runtime.environment.leases import RuntimeLeaseStore
from backend.app.runtime.environment.manager import RuntimeManager
from backend.app.runtime.environment.metadata import (
    default_runtime_hardening_policy,
    runtime_hardening_metadata,
    runtime_isolation_metadata,
    runtime_labels,
)
from backend.app.runtime.environment.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.runtime.environment.policies.safety import is_digest_pinned_image
from backend.app.runtime.environment.pool.leases import RuntimePoolLeaseStore
from backend.app.runtime.environment.pool.policy import pooled_isolation_metadata
from backend.app.runtime.environment.pool.reclaim import RuntimePoolReclaimer
from backend.app.runtime.environment.pool.reset import RuntimePoolResetService
from backend.app.runtime.environment.pool.service import (
    MANAGED_RUNTIME_PROVIDER,
    RuntimePoolService,
)

_RUNTIME_WORKSPACE_ROOT = "/workspace"


@dataclass(frozen=True, slots=True)
class RunRuntimeEnvironmentResult:
    runtime: WorkspaceRuntime | None
    created: bool


@dataclass(frozen=True, slots=True)
class _IsolatedRuntimeSpec:
    child: WorkspaceRuntime
    template: RuntimeTemplate
    limits: RuntimeLimits
    network_policy: dict[str, object]
    network_disabled: bool
    hardening: RuntimeHardeningPolicy
    volume_name: str
    isolation: dict[str, object]
    persistent_mounts: tuple[RuntimeMount, ...]


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
        self._template(parent)
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
        docker = self._require_docker()
        existing = self._existing_for_run(run, parent)
        if existing is not None:
            return self._reuse_isolated_runtime(run, parent, existing, docker)

        spec = self._prepare_isolated_runtime(run, parent)
        container_id = self._provision_isolated_runtime(run, parent, spec, docker)
        self._activate_isolated_runtime(run, parent, spec, container_id)
        return RunRuntimeEnvironmentResult(runtime=spec.child, created=True)

    def _reuse_isolated_runtime(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
        existing: WorkspaceRuntime,
        docker: DockerRuntimeClient,
    ) -> RunRuntimeEnvironmentResult:
        self._require_parent_binding(existing, parent, run)
        if existing.status in {"active", "running"} and existing.connection_status == "online":
            run.execution_runtime_id = existing.id
            return RunRuntimeEnvironmentResult(runtime=existing, created=False)
        if existing.status in {"created", "provisioning"} and existing.docker_container_id:
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

    def _prepare_isolated_runtime(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> _IsolatedRuntimeSpec:
        template = self._template(parent)
        limits = _runtime_limits(parent.limits)
        network_policy = dict(parent.network_policy or {})
        network_disabled = _network_disabled(network_policy)
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
        hardening = default_runtime_hardening_policy()
        hardening_metadata = runtime_hardening_metadata(
            hardening,
            isolation_metadata=isolation,
        )
        identity = self._run_identity_metadata(run)
        child.capabilities = {
            "isolation": isolation,
            "hardening": hardening_metadata,
            "execution": {
                "mode": "isolated",
                "run_id": str(run.id),
                "parent_runtime_id": str(parent.id),
                "cleanup_status": "pending",
                "identity": identity,
                "persistent_mounts": [_mount_metadata(mount) for mount in persistent_mounts],
            },
            "managed_resources": {"docker_volumes": [volume_name]},
        }
        return _IsolatedRuntimeSpec(
            child=child,
            template=template,
            limits=limits,
            network_policy=network_policy,
            network_disabled=network_disabled,
            hardening=hardening,
            volume_name=volume_name,
            isolation=isolation,
            persistent_mounts=persistent_mounts,
        )

    def _provision_isolated_runtime(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
        spec: _IsolatedRuntimeSpec,
        docker: DockerRuntimeClient,
    ) -> str:
        container_id: str | None = None
        try:
            container_id = docker.create_container(
                RuntimeCreateRequest(
                    image=spec.template.image,
                    name=f"opsmesh-run-{run.workspace_id.hex[:12]}-{run.id.hex}",
                    workspace_id=str(run.workspace_id),
                    runtime_id=str(spec.child.id),
                    runtime_space_id=(
                        str(parent.runtime_space_id)
                        if parent.runtime_space_id is not None
                        else None
                    ),
                    limits=spec.limits,
                    network_disabled=spec.network_disabled,
                    network_policy=spec.network_policy,
                    labels={
                        **runtime_labels(spec.child),
                        **self._run_identity_labels(run),
                        "opsmesh.run_id": str(run.id),
                        "opsmesh.parent_runtime_id": str(parent.id),
                    },
                    mounts=(
                        RuntimeMount(source=spec.volume_name, target=_RUNTIME_WORKSPACE_ROOT),
                        *spec.persistent_mounts,
                    ),
                    hardening=spec.hardening,
                    working_dir=_RUNTIME_WORKSPACE_ROOT,
                )
            )
            docker.start_container(container_id)
            return container_id
        except Exception as exc:
            self._discard_failed_child(spec.child, container_id, spec.volume_name)
            raise RuntimeEnvironmentError(
                "runtime_execution_provisioning_failed",
                "The per-run execution runtime could not be provisioned",
            ) from exc

    def _activate_isolated_runtime(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
        spec: _IsolatedRuntimeSpec,
        container_id: str,
    ) -> None:
        child = spec.child
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
                "isolation": spec.isolation,
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
                "isolation": spec.isolation,
            },
        )
        event_metadata = {
            "execution_runtime_id": str(child.id),
            "parent_runtime_id": str(parent.id),
            "persistent_mount_count": len(spec.persistent_mounts),
        }
        RunEventRecorder(self._session).append_event(
            run,
            "run.runtime_environment.created",
            "Per-run isolated execution environment created",
            event_metadata,
        )
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="run.runtime_environment.created",
            target_type="agent_run",
            target_id=run.id,
            metadata=event_metadata,
        )
        run.execution_runtime_id = child.id
        _set_run_execution_metadata(run, status="active", runtime_id=child.id)
        self._session.flush([child, run])

    def _ensure_persistent_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> RunRuntimeEnvironmentResult:
        was_active = (
            _runtime_execution_mode(run) == "persistent"
            and _runtime_execution_status(run) == "active"
        )
        lease = RuntimePoolLeaseStore(self._session).acquire(
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

        acquisition = RuntimePoolService(self._session).acquire(parent, run)
        if acquisition is None:
            raise RuntimeEnvironmentError(
                "runtime_pool_exhausted",
                "No available pooled runtime container matches the run policy",
            )
        member = acquisition.member
        now = datetime.now(UTC)
        isolation = pooled_isolation_metadata(parent, member, run)
        identity = self._run_identity_metadata(run)
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
                    "identity": identity,
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

    def _parent_for_run(self, run: AgentRun) -> WorkspaceRuntime | None:
        if run.runtime_id is None:
            return None
        return self._runtime(run.workspace_id, run.runtime_id)

    def _require_managed_parent(self, parent: WorkspaceRuntime) -> None:
        if parent.runtime_provider != MANAGED_RUNTIME_PROVIDER:
            raise RuntimeEnvironmentError(
                "runtime_provider_unsupported",
                "The selected runtime provider does not expose managed execution",
            )
        if not isinstance(parent.capabilities.get("isolation"), dict):
            raise RuntimeEnvironmentError(
                "runtime_isolation_unverified",
                "Managed runtime has no platform isolation evidence",
            )
        self._require_docker()

    def _require_docker(self) -> DockerRuntimeClient:
        if self._docker is None:
            raise RuntimeEnvironmentError(
                "runtime_execution_client_missing",
                "Managed run execution requires a worker-injected Docker client",
            )
        return self._docker

    def _run_identity_labels(self, run: AgentRun) -> dict[str, str]:
        return {
            f"opsmesh.{key}": value
            for key, value in self._run_identity_metadata(run).items()
        }

    def _run_identity_metadata(self, run: AgentRun) -> dict[str, str]:
        labels: dict[str, str] = {}
        if run.task_id is None:
            return labels
        task = self._session.scalar(
            select(Task).where(
                Task.workspace_id == run.workspace_id,
                Task.id == run.task_id,
            )
        )
        if task is None:
            raise RuntimeEnvironmentError(
                "runtime_task_unavailable",
                "The run task is not available in the workspace",
            )
        labels["task_id"] = str(task.id)
        if task.agent_team_id is not None:
            labels["team_id"] = str(task.agent_team_id)
        if task.workspace_project_id is not None:
            labels["project_id"] = str(task.workspace_project_id)
        return labels

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
            RuntimePoolService(self._session).release(member, run)

    def reclaim_orphaned_pool_leases(
        self,
        *,
        workspace_id: UUID | None = None,
        stale_after_seconds: int = 600,
        limit: int = 100,
    ) -> tuple[int, int]:
        return RuntimePoolReclaimer(
            self._session,
            self._docker,
            cleanup_run=self.cleanup_for_run,
            record_failed_run=self._record_failed_reclaimed_run,
        ).reclaim(
            workspace_id=workspace_id,
            stale_after_seconds=stale_after_seconds,
            limit=limit,
        )

    def _record_failed_reclaimed_run(
        self,
        run: AgentRun,
        runtime_id: UUID,
        member_id: UUID,
    ) -> None:
        _set_run_execution_metadata(
            run,
            status="failed",
            runtime_id=runtime_id,
            mode="pooled",
            pool_member_runtime_id=member_id,
        )
        self._record_cleanup(run, status="failed", runtime_id=runtime_id)

    def _release_persistent_for_run(
        self,
        run: AgentRun,
        parent: WorkspaceRuntime,
    ) -> bool:
        try:
            RuntimePoolService(self._session).release(parent, run)
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
            RuntimePoolResetService(self._session, self._docker).reset(member, run)
        except Exception:
            self._destroy_failed_pool_member(member, child, run)
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
        RuntimePoolService(self._session).release(member, run)
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

    def _destroy_failed_pool_member(
        self,
        member: WorkspaceRuntime,
        child: WorkspaceRuntime,
        run: AgentRun,
    ) -> None:
        deleted = RuntimePoolResetService(
            self._session,
            self._docker,
        ).destroy_failed_member(member, child)
        _set_run_execution_metadata(
            run,
            status="completed" if deleted else "failed",
            runtime_id=child.id,
            mode="pooled",
            pool_member_runtime_id=member.id,
        )
        self._record_cleanup(
            run,
            status="completed" if deleted else "failed",
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
        if not is_digest_pinned_image(template.image):
            raise RuntimeEnvironmentError(
                "runtime_image_digest_required",
                "Runtime images must be pinned by an immutable sha256 digest",
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


def _mount_metadata(mount: RuntimeMount) -> dict[str, object]:
    return {
        "source": mount.source,
        "target": mount.target,
        "type": mount.mount_type,
        "read_only": mount.read_only,
    }


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
