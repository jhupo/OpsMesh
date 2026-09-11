from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCreateRequest,
    RuntimeLimits,
    RuntimeMount,
)
from backend.app.runtime_manager.events import RuntimeEventLog
from backend.app.runtime_manager.leases import RuntimeLeaseStore
from backend.app.runtime_manager.manager import RuntimeManager
from backend.app.runtime_manager.metadata import (
    default_runtime_hardening_policy,
    runtime_isolation_metadata,
    runtime_labels,
)
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime

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
    """Own the short-lived Docker boundary used by one managed agent run.

    A workspace runtime is a durable placement and policy anchor. It is never used as the
    execution filesystem directly: each run receives a fresh container and volume, while only
    explicitly declared persistent mounts may be copied into that container. The parent runtime
    remains the source of the immutable image, network, hardening, and quota policy.
    """

    def __init__(self, session: Session, docker_client: DockerRuntimeClient | None) -> None:
        self._session = session
        self._docker = docker_client

    def ensure_for_run(self, run: AgentRun) -> RunRuntimeEnvironmentResult:
        if run.runtime_id is None:
            return RunRuntimeEnvironmentResult(runtime=None, created=False)
        parent = self._runtime(run.workspace_id, run.runtime_id)
        if parent.runtime_provider not in MANAGED_RUNTIME_PROVIDERS:
            return RunRuntimeEnvironmentResult(runtime=None, created=False)
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

        existing = self._existing_for_run(run, parent)
        if existing is not None:
            self._require_parent_binding(existing, parent, run)
            if existing.status in {"active", "running"} and existing.connection_status == "online":
                run.execution_runtime_id = existing.id
                return RunRuntimeEnvironmentResult(runtime=existing, created=False)
            if existing.status in {"created", "provisioning"} and existing.docker_container_id:
                self._docker.start_container(existing.docker_container_id)
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
            "mode": "per_run",
            "run_id": str(run.id),
            "parent_runtime_id": str(parent.id),
            "ephemeral": True,
        }
        persistent_mounts = _persistent_mounts(parent.capabilities)
        child.capabilities = {
            "isolation": isolation,
            "hardening": dict(parent.capabilities.get("hardening") or {}),
            "execution": {
                "mode": "per_run",
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
            container_id = self._docker.create_container(
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
            self._docker.start_container(container_id)
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
                "scope": "per_run",
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

    def cleanup_for_run(self, run: AgentRun) -> bool:
        execution_runtime_id = run.execution_runtime_id
        if execution_runtime_id is None:
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
            _set_run_execution_metadata(run, status="completed", runtime_id=child.id)
            return True
        if self._docker is None:
            _set_run_execution_metadata(run, status="failed", runtime_id=child.id)
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return False
        try:
            RuntimeManager(self._session, self._docker).delete_runtime(child)
        except Exception:
            _set_run_execution_metadata(run, status="failed", runtime_id=child.id)
            self._record_cleanup(run, status="failed", runtime_id=child.id)
            return False
        _set_run_execution_metadata(run, status="completed", runtime_id=child.id)
        self._record_cleanup(run, status="completed", runtime_id=child.id)
        return True

    def _record_cleanup(self, run: AgentRun, *, status: str, runtime_id: UUID) -> None:
        metadata = {
            "execution_runtime_id": str(runtime_id),
            "cleanup_status": status,
        }
        event_type = (
            "run.runtime_environment.cleaned"
            if status in {"completed", "not_required"}
            else "run.runtime_environment.cleanup_failed"
        )
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
            "Per-run execution runtime cleanup completed"
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
        execution = dict(run_input.get("runtime_execution") or {})
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
    values = {
        "cpu_count": raw.get("cpu_count"),
        "memory_mb": raw.get("memory_mb"),
        "disk_mb": raw.get("disk_mb"),
        "timeout_seconds": raw.get("timeout_seconds"),
        "max_output_bytes": raw.get("max_output_bytes", 256_000),
        "max_processes": raw.get("max_processes", 256),
    }
    if not isinstance(values["cpu_count"], int | float) or values["cpu_count"] <= 0:
        raise RuntimeEnvironmentError(
            "runtime_limits_invalid",
            "Parent runtime CPU limit is invalid",
        )
    for key in ("memory_mb", "disk_mb", "timeout_seconds", "max_output_bytes", "max_processes"):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeEnvironmentError(
                "runtime_limits_invalid",
                f"Parent runtime {key} limit is invalid",
            )
    return RuntimeLimits(
        cpu_count=float(values["cpu_count"]),
        memory_mb=int(values["memory_mb"]),
        disk_mb=int(values["disk_mb"]),
        timeout_seconds=int(values["timeout_seconds"]),
        max_output_bytes=int(values["max_output_bytes"]),
        max_processes=int(values["max_processes"]),
    )


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


def _set_run_execution_metadata(run: AgentRun, *, status: str, runtime_id: UUID) -> None:
    run_input = dict(run.input or {})
    execution = dict(run_input.get("runtime_execution") or {})
    execution.update({"runtime_id": str(runtime_id), "status": status})
    run_input["runtime_execution"] = execution
    run.input = run_input
