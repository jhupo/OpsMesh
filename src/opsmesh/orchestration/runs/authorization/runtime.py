from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentRuntimeExecutionBinding
from opsmesh.orchestration.runs.authorization.policy import (
    RunRuntimeAuthorizationError,
    catalog_for_snapshot,
    catalog_requires_stdio_runtime,
    file_resource_ids,
    parse_required_uuid,
    parse_uuid_set,
    runtime_policy_disables_network,
    runtime_resource_grants,
    snapshot_file_scope_ids,
)
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.runtime.instances.allocations import RuntimeAllocationStore
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.pools.policy import shared_host_policy_matches
from opsmesh.runtime.spaces.models import RuntimeSpace, RuntimeSpaceBinding
from opsmesh.teams.management.models import AgentTeam
from opsmesh.teams.sessions.refs import team_bound_runtime_id

RUNTIME_READY_STATUSES = frozenset({"active", "running"})


@dataclass(frozen=True, slots=True)
class ResolvedRunRuntimeBinding:
    mode: str
    workspace_id: UUID
    workspace_runtime_id: UUID | None
    runtime_space_id: UUID | None
    capability_resource_ids: tuple[UUID, ...]
    network_disabled: bool
    allowed_file_ids: tuple[UUID, ...]
    execution_runtime_id: UUID | None = None

    def as_snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "workspace_id": str(self.workspace_id),
            "workspace_runtime_id": (
                str(self.workspace_runtime_id) if self.workspace_runtime_id is not None else None
            ),
            "runtime_space_id": (
                str(self.runtime_space_id) if self.runtime_space_id is not None else None
            ),
            "capability_resource_ids": [
                str(resource_id) for resource_id in self.capability_resource_ids
            ],
            "network_disabled": self.network_disabled,
            "file_access_scope": {
                "mode": "gateway_only",
                "allowed_file_ids": [str(file_id) for file_id in self.allowed_file_ids],
            },
        }

    def as_runtime_context(self) -> AgentRuntimeExecutionBinding:
        return AgentRuntimeExecutionBinding(
            mode=self.mode,
            workspace_runtime_id=self.workspace_runtime_id,
            runtime_space_id=self.runtime_space_id,
            capability_resource_ids=self.capability_resource_ids,
            network_disabled=self.network_disabled,
            allowed_file_ids=self.allowed_file_ids,
            execution_runtime_id=self.execution_runtime_id,
        )


class RunRuntimeAuthorizationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_for_snapshot(
        self,
        *,
        task: Task,
        step: TaskStep | None,
        capability_catalog: dict[str, object] | None,
        runtime_policy: dict[str, object],
    ) -> ResolvedRunRuntimeBinding:
        try:
            runtime_resource_ids, granted_runtime_ids, granted_space_ids = runtime_resource_grants(
                capability_catalog
            )
        except ValueError as exc:
            raise RunRuntimeAuthorizationError(
                "runtime_grant_invalid",
                "Effective runtime resource grant is invalid",
            ) from exc
        if len(granted_runtime_ids) > 1 or len(granted_space_ids) > 1:
            raise RunRuntimeAuthorizationError(
                "runtime_grant_ambiguous",
                "Effective capability policy grants more than one runtime placement",
            )

        team = self._team_for_task(task)
        team_runtime_id = team_bound_runtime_id(team) if team is not None else None
        granted_runtime_id = _only(granted_runtime_ids)
        if (
            granted_runtime_id is not None
            and team_runtime_id is not None
            and granted_runtime_id != team_runtime_id
        ):
            raise RunRuntimeAuthorizationError(
                "runtime_grant_conflicts_with_team_runtime",
                "Capability runtime grant conflicts with the team's bound runtime",
            )
        runtime_id = granted_runtime_id or team_runtime_id
        runtime = self._runtime(task.workspace_id, runtime_id) if runtime_id is not None else None
        if runtime is not None:
            self._require_runtime_ready(runtime)

        requested_space_id = (
            step.runtime_space_id if step is not None else None
        ) or task.runtime_space_id
        granted_space_id = _only(granted_space_ids)
        if (
            requested_space_id is not None
            and granted_space_id is not None
            and requested_space_id != granted_space_id
        ):
            raise RunRuntimeAuthorizationError(
                "runtime_space_grant_conflict",
                "Capability runtime-space grant conflicts with the task placement",
            )
        runtime_space_id = (
            requested_space_id
            or granted_space_id
            or (runtime.runtime_space_id if runtime is not None else None)
            or (team.runtime_space_id if team is not None else None)
        )
        if runtime is not None and runtime_space_id != runtime.runtime_space_id:
            raise RunRuntimeAuthorizationError(
                "runtime_space_mismatch",
                "Selected runtime does not belong to the authorized runtime space",
            )
        runtime_space = (
            self._runtime_space(task.workspace_id, runtime_space_id)
            if runtime_space_id is not None
            else None
        )
        if runtime_space is not None:
            self._require_space_available_for_task(runtime_space, task)

        if catalog_requires_stdio_runtime(capability_catalog) and runtime is None:
            raise RunRuntimeAuthorizationError(
                "stdio_runtime_required",
                "An active concrete runtime is required for MCP stdio tools",
            )

        try:
            file_ids = self._effective_file_ids(task, step, capability_catalog)
        except ValueError as exc:
            raise RunRuntimeAuthorizationError(
                "runtime_file_scope_invalid",
                "Effective runtime file scope is invalid",
            ) from exc
        requested_network_disabled = runtime_policy_disables_network(runtime_policy)
        space_network_disabled = (
            runtime_policy_disables_network({"network": runtime_space.network_policy})
            if runtime_space is not None
            else False
        )
        actual_network_disabled = (
            runtime.network_policy.get("mode") == "none" if runtime is not None else False
        )
        network_disabled = (
            requested_network_disabled or space_network_disabled or actual_network_disabled
        )
        if runtime is not None and network_disabled and not actual_network_disabled:
            raise RunRuntimeAuthorizationError(
                "runtime_network_policy_mismatch",
                "Selected runtime does not enforce the required disabled network policy",
            )

        mode = "none"
        if runtime_resource_ids:
            mode = "capability_runtime"
        elif runtime is not None:
            mode = "team_runtime"
        elif runtime_space is not None:
            mode = "runtime_space"
        return ResolvedRunRuntimeBinding(
            mode=mode,
            workspace_id=task.workspace_id,
            workspace_runtime_id=runtime.id if runtime is not None else None,
            runtime_space_id=runtime_space.id if runtime_space is not None else None,
            capability_resource_ids=tuple(sorted(runtime_resource_ids, key=str)),
            network_disabled=network_disabled,
            allowed_file_ids=file_ids,
        )

    def validate_for_run(
        self,
        *,
        run: AgentRun,
        task: Task | None,
        snapshot: dict[str, object],
    ) -> ResolvedRunRuntimeBinding:
        binding = runtime_binding_for_snapshot(snapshot, workspace_id=run.workspace_id)
        if binding is None:
            try:
                runtime_resource_ids, _, _ = runtime_resource_grants(catalog_for_snapshot(snapshot))
                granted_file_ids = file_resource_ids(catalog_for_snapshot(snapshot))
            except ValueError as exc:
                raise RunRuntimeAuthorizationError(
                    "runtime_binding_invalid",
                    "Frozen runtime binding cannot be verified",
                ) from exc
            if (
                run.runtime_id is not None
                or run.execution_runtime_id is not None
                or run.runtime_space_id is not None
                or runtime_resource_ids
                or granted_file_ids
                or catalog_requires_stdio_runtime(catalog_for_snapshot(snapshot))
            ):
                raise RunRuntimeAuthorizationError(
                    "runtime_binding_missing",
                    "A run with runtime or file capability grants requires "
                    "a frozen runtime binding",
                )
            return ResolvedRunRuntimeBinding(
                mode="none",
                workspace_id=run.workspace_id,
                workspace_runtime_id=None,
                runtime_space_id=None,
                capability_resource_ids=(),
                network_disabled=False,
                allowed_file_ids=(),
            )
        if binding.workspace_runtime_id != run.runtime_id:
            raise RunRuntimeAuthorizationError(
                "runtime_binding_mismatch",
                "Run runtime does not match the frozen authorization binding",
            )
        if binding.runtime_space_id != run.runtime_space_id:
            raise RunRuntimeAuthorizationError(
                "runtime_space_binding_mismatch",
                "Run runtime space does not match the frozen authorization binding",
            )
        if binding.mode == "team_runtime":
            team = self._team_for_task(task) if task is not None else None
            if team is None or team_bound_runtime_id(team) != binding.workspace_runtime_id:
                raise RunRuntimeAuthorizationError(
                    "team_runtime_binding_revoked",
                    "The team's frozen runtime binding is no longer active",
                )
        try:
            expected_resource_ids, _, _ = runtime_resource_grants(catalog_for_snapshot(snapshot))
            catalog_file_ids = file_resource_ids(catalog_for_snapshot(snapshot))
            snapshot_file_ids = snapshot_file_scope_ids(snapshot)
        except ValueError as exc:
            raise RunRuntimeAuthorizationError(
                "runtime_binding_invalid",
                "Frozen runtime binding cannot be verified",
            ) from exc
        if set(binding.capability_resource_ids) != expected_resource_ids:
            raise RunRuntimeAuthorizationError(
                "runtime_resource_binding_mismatch",
                "Frozen runtime resource provenance does not match the capability catalog",
            )
        if set(binding.allowed_file_ids) != snapshot_file_ids or not set(
            binding.allowed_file_ids
        ).issubset(catalog_file_ids):
            raise RunRuntimeAuthorizationError(
                "runtime_file_scope_mismatch",
                "Frozen runtime file scope does not match the capability catalog",
            )
        if binding.workspace_runtime_id is not None:
            runtime = self._runtime(run.workspace_id, binding.workspace_runtime_id)
            self._require_runtime_ready(runtime)
            if runtime.runtime_space_id != binding.runtime_space_id:
                raise RunRuntimeAuthorizationError(
                    "runtime_space_mismatch",
                    "Runtime space changed after the run was authorized",
                )
            if binding.network_disabled and runtime.network_policy.get("mode") != "none":
                raise RunRuntimeAuthorizationError(
                    "runtime_network_policy_mismatch",
                    "Runtime no longer enforces the frozen network policy",
                )
        execution_runtime_id = self._execution_runtime_for_run(run, binding)
        if binding.runtime_space_id is not None:
            runtime_space = self._runtime_space(run.workspace_id, binding.runtime_space_id)
            if task is None:
                if runtime_space.status != "active":
                    raise RunRuntimeAuthorizationError(
                        "runtime_space_unavailable",
                        "Frozen runtime space is not active",
                    )
            else:
                self._require_space_available_for_task(runtime_space, task)
        self._require_active_files(run.workspace_id, binding.allowed_file_ids)
        return replace(binding, execution_runtime_id=execution_runtime_id)

    def _execution_runtime_for_run(
        self,
        run: AgentRun,
        binding: ResolvedRunRuntimeBinding,
    ) -> UUID | None:
        if run.execution_runtime_id is None:
            return None
        if binding.workspace_runtime_id is None:
            raise RunRuntimeAuthorizationError(
                "runtime_execution_binding_invalid",
                "A per-run runtime requires an authorized parent runtime",
            )
        execution_runtime = self._runtime(run.workspace_id, run.execution_runtime_id)
        parent = self._runtime(run.workspace_id, binding.workspace_runtime_id)
        if execution_runtime.execution_mode == "isolated":
            valid = (
                execution_runtime.parent_runtime_id == parent.id
                and execution_runtime.execution_run_id == run.id
            )
        else:
            valid = (
                execution_runtime.execution_run_id is None
                and execution_runtime.runtime_space_id == binding.runtime_space_id
                and execution_runtime.execution_mode == parent.execution_mode
                and (
                    execution_runtime.id == parent.id
                    or (
                        parent.execution_mode == "shared"
                        and shared_host_policy_matches(parent, execution_runtime)
                    )
                )
                and RuntimeAllocationStore(self._session).get(execution_runtime, "run", run.id)
                is not None
            )
        if not valid:
            raise RunRuntimeAuthorizationError(
                "runtime_execution_binding_invalid",
                "Run has no authorized execution slot",
            )
        self._require_runtime_ready(execution_runtime)
        if binding.network_disabled and execution_runtime.network_policy.get("mode") != "none":
            raise RunRuntimeAuthorizationError(
                "runtime_execution_network_policy_mismatch",
                "Runtime violates the frozen network policy",
            )
        return execution_runtime.id

    def _team_for_task(self, task: Task) -> AgentTeam | None:
        if task.agent_team_id is None:
            return None
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == task.workspace_id,
                AgentTeam.id == task.agent_team_id,
                AgentTeam.status == "active",
            )
        )

    def _runtime(self, workspace_id: UUID, runtime_id: UUID) -> WorkspaceRuntime:
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == runtime_id,
            )
        )
        if runtime is None:
            raise RunRuntimeAuthorizationError(
                "runtime_unavailable",
                "Authorized runtime was not found in the workspace",
            )
        return runtime

    def _runtime_space(self, workspace_id: UUID, runtime_space_id: UUID) -> RuntimeSpace:
        runtime_space = self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )
        if runtime_space is None:
            raise RunRuntimeAuthorizationError(
                "runtime_space_unavailable",
                "Authorized runtime space was not found in the workspace",
            )
        return runtime_space

    def _require_runtime_ready(self, runtime: WorkspaceRuntime) -> None:
        if runtime.capabilities.get("plugin_install_id"):
            raise RunRuntimeAuthorizationError(
                "runtime_reserved", "Plugin processes cannot be used as agent sandboxes"
            )
        if runtime.status not in RUNTIME_READY_STATUSES or runtime.connection_status != "online":
            raise RunRuntimeAuthorizationError(
                "runtime_unavailable",
                "Authorized runtime is not active and online",
            )

    def _require_space_available_for_task(
        self,
        runtime_space: RuntimeSpace,
        task: Task,
    ) -> None:
        if runtime_space.status != "active":
            raise RunRuntimeAuthorizationError(
                "runtime_space_unavailable",
                "Authorized runtime space is not active",
            )
        if runtime_space.scope == "workspace":
            return
        target_type = "agent_team" if runtime_space.scope == "team" else "task"
        target_id = task.agent_team_id if runtime_space.scope == "team" else task.id
        if target_id is None:
            raise RunRuntimeAuthorizationError(
                "runtime_space_target_mismatch",
                "Runtime space is not bound to this task context",
            )
        binding_id = self._session.scalar(
            select(RuntimeSpaceBinding.id).where(
                RuntimeSpaceBinding.workspace_id == task.workspace_id,
                RuntimeSpaceBinding.runtime_space_id == runtime_space.id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if binding_id is None:
            raise RunRuntimeAuthorizationError(
                "runtime_space_target_mismatch",
                "Runtime space is not actively bound to this task context",
            )

    def _effective_file_ids(
        self,
        task: Task,
        step: TaskStep | None,
        capability_catalog: dict[str, object] | None,
    ) -> tuple[UUID, ...]:
        granted_ids = file_resource_ids(capability_catalog)
        dependency_ids = parse_uuid_set(
            step.dependencies.get("allowed_file_ids")
            if step is not None and isinstance(step.dependencies, dict)
            else None,
            field="task step allowed_file_ids",
        )
        effective_ids = granted_ids & dependency_ids if dependency_ids else granted_ids
        self._require_active_files(task.workspace_id, tuple(effective_ids))
        return tuple(sorted(effective_ids, key=str))

    def _require_active_files(self, workspace_id: UUID, file_ids: tuple[UUID, ...]) -> None:
        if not file_ids:
            return
        active_ids = set(
            self._session.scalars(
                select(WorkspaceFile.id).where(
                    WorkspaceFile.workspace_id == workspace_id,
                    WorkspaceFile.status == "active",
                    WorkspaceFile.id.in_(file_ids),
                )
            ).all()
        )
        if any(file_id not in active_ids for file_id in file_ids):
            raise RunRuntimeAuthorizationError(
                "runtime_file_scope_unavailable",
                "Frozen runtime file scope references an unavailable workspace file",
            )


def runtime_binding_for_snapshot(
    snapshot: dict[str, object],
    *,
    workspace_id: UUID,
) -> ResolvedRunRuntimeBinding | None:
    raw = snapshot.get("runtime_binding")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RunRuntimeAuthorizationError(
            "runtime_binding_invalid",
            "Frozen runtime binding is invalid",
        )
    try:
        binding_workspace_id = UUID(str(raw.get("workspace_id")))
        runtime_id = _optional_uuid(raw.get("workspace_runtime_id"))
        runtime_space_id = _optional_uuid(raw.get("runtime_space_id"))
        capability_resource_ids = tuple(
            sorted(
                parse_uuid_set(
                    raw.get("capability_resource_ids"),
                    field="runtime binding capability_resource_ids",
                ),
                key=str,
            )
        )
        file_access_scope = raw.get("file_access_scope")
        if not isinstance(file_access_scope, dict):
            raise ValueError("runtime binding file_access_scope must be an object")
        if file_access_scope.get("mode") != "gateway_only":
            raise ValueError("runtime binding file access mode is unsupported")
        allowed_file_ids = tuple(
            sorted(
                parse_uuid_set(
                    file_access_scope.get("allowed_file_ids"),
                    field="runtime binding allowed_file_ids",
                ),
                key=str,
            )
        )
    except (TypeError, ValueError) as exc:
        raise RunRuntimeAuthorizationError(
            "runtime_binding_invalid",
            "Frozen runtime binding is invalid",
        ) from exc
    mode = raw.get("mode")
    network_disabled = raw.get("network_disabled")
    if (
        mode not in {"none", "team_runtime", "capability_runtime", "runtime_space"}
        or not isinstance(network_disabled, bool)
        or binding_workspace_id != workspace_id
    ):
        raise RunRuntimeAuthorizationError(
            "runtime_binding_invalid",
            "Frozen runtime binding is invalid",
        )
    if (
        (mode == "none" and (runtime_id is not None or runtime_space_id is not None))
        or (mode == "team_runtime" and runtime_id is None)
        or (mode == "capability_runtime" and not capability_resource_ids)
        or (mode == "runtime_space" and (runtime_space_id is None or runtime_id is not None))
    ):
        raise RunRuntimeAuthorizationError(
            "runtime_binding_invalid",
            "Frozen runtime binding mode does not match its placement",
        )
    return ResolvedRunRuntimeBinding(
        mode=mode,
        workspace_id=binding_workspace_id,
        workspace_runtime_id=runtime_id,
        runtime_space_id=runtime_space_id,
        capability_resource_ids=capability_resource_ids,
        network_disabled=network_disabled,
        allowed_file_ids=allowed_file_ids,
    )


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else parse_required_uuid(value, "runtime binding identifier")


def _only(values: set[UUID]) -> UUID | None:
    return next(iter(values)) if values else None
