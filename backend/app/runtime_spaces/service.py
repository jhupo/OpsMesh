from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageParams
from backend.app.api.schemas.runtime_spaces import (
    RuntimeSpaceBlockedStepDiagnosticResponse,
    RuntimeSpaceDiagnosticsResponse,
    RuntimeSpaceQuotaDiagnosticResponse,
    RuntimeSpaceReservationDiagnosticResponse,
    RuntimeSpaceResponse,
    RuntimeSpaceRuntimeDiagnosticResponse,
)
from backend.app.orchestration.blocked_reasons import explain_blocked_reason
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam

TARGET_TYPES_BY_SCOPE = {
    "workspace": "workspace",
    "team": "agent_team",
    "task": "task",
}
T = TypeVar("T")
RUN_CAPACITY_QUOTA_KEY = "active_runs"


@dataclass(frozen=True)
class RuntimeSpaceReservationResult:
    reservation: RuntimeSpaceReservation | None
    blocked_reason: str | None = None


class RuntimeSpaceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_runtime_space(
        self,
        *,
        workspace_id: UUID,
        name: str,
        scope: str,
        target_id: UUID | None,
        created_by_user_id: UUID | None,
        default_runtime_template_id: UUID | None,
        policy: dict[str, object],
        network_policy: dict[str, object],
        storage_policy: dict[str, object],
        cleanup_policy: dict[str, object],
        quota_limits: dict[str, int],
    ) -> RuntimeSpace:
        normalized_target_id = self._normalize_target_id(
            workspace_id=workspace_id,
            scope=scope,
            target_id=target_id,
        )
        if default_runtime_template_id is not None:
            self._require_runtime_template(default_runtime_template_id)

        runtime_space = RuntimeSpace(
            workspace_id=workspace_id,
            created_by_user_id=created_by_user_id,
            default_runtime_template_id=default_runtime_template_id,
            name=name,
            scope=scope,
            policy=policy,
            network_policy=network_policy,
            storage_policy=storage_policy,
            cleanup_policy=cleanup_policy,
        )
        self._session.add(runtime_space)
        self._session.flush([runtime_space])
        self._bind_target(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            target_id=normalized_target_id,
        )
        self._replace_quotas(
            workspace_id=workspace_id,
            runtime_space=runtime_space,
            quota_limits=quota_limits,
        )
        self._append_event(
            runtime_space,
            "runtime_space.created",
            f"Runtime space {runtime_space.name} created",
            {
                "scope": scope,
                "target_id": str(normalized_target_id),
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def list_runtime_spaces(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        status: str | None = None,
        scope: str | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        statement = select(RuntimeSpace).where(RuntimeSpace.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(RuntimeSpace.status == status)
        if scope is not None:
            statement = statement.where(RuntimeSpace.scope == scope)
        return self._page(statement.order_by(RuntimeSpace.created_at.desc()), page)

    def get_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpace | None:
        return self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )

    def diagnostics(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpaceDiagnosticsResponse | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        quotas = self._session.scalars(
            select(RuntimeSpaceQuota)
            .where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
                RuntimeSpaceQuota.status == "active",
            )
            .order_by(RuntimeSpaceQuota.quota_key.asc())
        ).all()
        reservations = self._session.scalars(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.status == "active",
            )
            .order_by(RuntimeSpaceReservation.created_at.asc())
        ).all()
        runtimes = self._session.scalars(
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.runtime_space_id == runtime_space_id,
            )
            .order_by(WorkspaceRuntime.created_at.desc())
        ).all()
        blocked_steps = self._blocked_steps_for_runtime_space(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
        )
        return RuntimeSpaceDiagnosticsResponse(
            runtime_space=RuntimeSpaceResponse.model_validate(runtime_space),
            quotas=[_quota_diagnostic(quota) for quota in quotas],
            active_reservations=[
                RuntimeSpaceReservationDiagnosticResponse(
                    id=reservation.id,
                    reservation_key=reservation.reservation_key,
                    task_id=reservation.task_id,
                    task_step_id=reservation.task_step_id,
                    agent_run_id=reservation.agent_run_id,
                    resource_usage=dict(reservation.resource_usage or {}),
                    created_at=reservation.created_at,
                    expires_at=reservation.expires_at,
                )
                for reservation in reservations
            ],
            runtimes=[
                RuntimeSpaceRuntimeDiagnosticResponse(
                    id=runtime.id,
                    name=runtime.name,
                    runtime_provider=runtime.runtime_provider,
                    runtime_type=runtime.runtime_type,
                    status=runtime.status,
                    connection_status=runtime.connection_status,
                    has_docker_container=runtime.docker_container_id is not None,
                    limits=dict(runtime.limits or {}),
                    network_policy=dict(runtime.network_policy or {}),
                    capabilities=dict(runtime.capabilities or {}),
                    last_heartbeat_at=runtime.last_heartbeat_at,
                )
                for runtime in runtimes
            ],
            blocked_steps=blocked_steps,
        )

    def update_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        name: str | None,
        status: str | None,
        policy: dict[str, object] | None,
        network_policy: dict[str, object] | None,
        storage_policy: dict[str, object] | None,
        cleanup_policy: dict[str, object] | None,
        quota_limits: dict[str, int] | None,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        changed_fields: list[str] = []
        for field_name, value in (
            ("name", name),
            ("status", status),
            ("policy", policy),
            ("network_policy", network_policy),
            ("storage_policy", storage_policy),
            ("cleanup_policy", cleanup_policy),
        ):
            if value is not None:
                setattr(runtime_space, field_name, value)
                changed_fields.append(field_name)
        if quota_limits is not None:
            self._replace_quotas(
                workspace_id=workspace_id,
                runtime_space=runtime_space,
                quota_limits=quota_limits,
            )
            changed_fields.append("quota_limits")
        if changed_fields:
            event_type = (
                "runtime_space.status_updated"
                if status is not None and runtime_space.status != old_status
                else "runtime_space.updated"
            )
            self._append_event(
                runtime_space,
                event_type,
                f"Runtime space {runtime_space.name} updated",
                {
                    "changed_fields": changed_fields,
                    "before_status": old_status,
                    "after_status": runtime_space.status,
                },
            )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def reset_runtime_space(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        self._append_event(
            runtime_space,
            "runtime_space.reset_requested",
            f"Runtime space {runtime_space.name} reset requested",
            {},
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def pause_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reason: str | None,
    ) -> RuntimeSpace | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        runtime_space.status = "paused"
        self._append_event(
            runtime_space,
            "runtime_space.paused",
            f"Runtime space {runtime_space.name} paused",
            {
                "before_status": old_status,
                "after_status": runtime_space.status,
                "reason": _non_empty_string_or_none(reason) or "operator_paused",
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def resume_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> tuple[RuntimeSpace, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        old_status = runtime_space.status
        runtime_space.status = "active"
        cleared = self._clear_runtime_space_pause_blocks(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space_id,
        )
        self._append_event(
            runtime_space,
            "runtime_space.resumed",
            f"Runtime space {runtime_space.name} resumed",
            {
                "before_status": old_status,
                "after_status": runtime_space.status,
                "cleared_blocked_steps": cleared,
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space, cleared

    def force_release_reservations(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str | None,
        reason: str | None,
    ) -> tuple[RuntimeSpace, int] | None:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None:
            return None
        statement = (
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        )
        normalized_key = _non_empty_string_or_none(reservation_key)
        if normalized_key is not None:
            statement = statement.where(RuntimeSpaceReservation.reservation_key == normalized_key)
        reservations = self._session.scalars(statement).all()
        if not reservations:
            self._append_event(
                runtime_space,
                "runtime_space.reservations_force_release_noop",
                f"No active runtime space reservations found for {runtime_space.name}",
                {
                    "reservation_key": normalized_key,
                    "reason": _non_empty_string_or_none(reason),
                },
            )
            self._session.commit()
            self._session.refresh(runtime_space)
            return runtime_space, 0

        quota_keys = {
            quota_key
            for reservation in reservations
            for quota_key in self._reservation_usage(reservation)
        }
        quotas: dict[str, RuntimeSpaceQuota] = {}
        if quota_keys:
            quotas = {
                quota.quota_key: quota
                for quota in self._session.scalars(
                    select(RuntimeSpaceQuota)
                    .where(
                        RuntimeSpaceQuota.workspace_id == workspace_id,
                        RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
                        RuntimeSpaceQuota.quota_key.in_(quota_keys),
                    )
                    .with_for_update()
                ).all()
            }
        released_at = datetime.now(UTC)
        released_keys: list[str] = []
        for reservation in reservations:
            released_keys.append(reservation.reservation_key)
            for quota_key, amount in self._reservation_usage(reservation).items():
                quota = quotas.get(quota_key)
                if quota is not None:
                    quota.reserved_value = max(0, quota.reserved_value - amount)
            reservation.status = "released"
            reservation.released_at = released_at
        self._append_event(
            runtime_space,
            "runtime_space.reservations_force_released",
            f"Force released {len(reservations)} runtime space reservations",
            {
                "reservation_key": normalized_key,
                "released_reservations": len(reservations),
                "released_keys": released_keys,
                "reason": _non_empty_string_or_none(reason),
            },
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space, len(reservations)

    def _blocked_steps_for_runtime_space(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> list[RuntimeSpaceBlockedStepDiagnosticResponse]:
        rows = self._session.execute(
            select(TaskStep, Task)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
                (
                    (TaskStep.runtime_space_id == runtime_space_id)
                    | (
                        TaskStep.runtime_space_id.is_(None)
                        & (Task.runtime_space_id == runtime_space_id)
                    )
                ),
            )
            .order_by(TaskStep.created_at.asc(), TaskStep.id.asc())
        ).all()
        blocked_steps: list[RuntimeSpaceBlockedStepDiagnosticResponse] = []
        for step, task in rows:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            explanation = explain_blocked_reason(dependencies.get("blocked_reason"))
            blocked_steps.append(
                RuntimeSpaceBlockedStepDiagnosticResponse(
                    task_step_id=step.id,
                    task_id=task.id,
                    task_title=task.title,
                    step_title=step.title,
                    reason=explanation.reason,
                    code=explanation.code,
                    message=explanation.message,
                    resource_key=explanation.resource_key,
                    blocked_resource_keys=_string_list(
                        dependencies.get("blocked_resource_keys")
                    ),
                    priority_score=_positive_int_or_none(dependencies.get("priority_score")),
                    created_at=step.created_at,
                )
            )
        return blocked_steps

    def list_events(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
        page: PageParams,
    ) -> tuple[list[RuntimeSpaceEvent], int] | None:
        if self.get_runtime_space(workspace_id, runtime_space_id) is None:
            return None
        statement = (
            select(RuntimeSpaceEvent)
            .where(
                RuntimeSpaceEvent.workspace_id == workspace_id,
                RuntimeSpaceEvent.runtime_space_id == runtime_space_id,
            )
            .order_by(RuntimeSpaceEvent.created_at.desc(), RuntimeSpaceEvent.id)
        )
        return self._page(statement, page)

    def require_runtime_space(self, workspace_id: UUID, runtime_space_id: UUID) -> RuntimeSpace:
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is None or runtime_space.status != "active":
            raise ValueError("Runtime space not found")
        return runtime_space

    def reserve_run_capacity(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        task_id: UUID | None,
        task_step_id: UUID | None,
        reservation_key: str,
        resource_usage: dict[str, int] | None = None,
    ) -> RuntimeSpaceReservationResult:
        usage = self._normalize_reservation_usage(resource_usage)
        runtime_space = self._session.scalar(
            select(RuntimeSpace)
            .where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
            .with_for_update()
        )
        if runtime_space is None or runtime_space.status != "active":
            return RuntimeSpaceReservationResult(
                reservation=None,
                blocked_reason="runtime_space_unavailable",
            )

        reservation = self._session.scalar(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.reservation_key == reservation_key,
            )
            .with_for_update()
        )
        if reservation is not None and reservation.status == "active":
            if not _active_reservation_matches(
                reservation=reservation,
                task_id=task_id,
                task_step_id=task_step_id,
                resource_usage=usage,
            ):
                return RuntimeSpaceReservationResult(
                    reservation=None,
                    blocked_reason="runtime_space_reservation_conflict",
                )
            return RuntimeSpaceReservationResult(reservation=reservation)

        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
                    RuntimeSpaceQuota.status == "active",
                    RuntimeSpaceQuota.quota_key.in_(usage),
                )
                .with_for_update()
            ).all()
        }
        exceeded_quota = self._first_exceeded_quota(quotas, usage)
        if exceeded_quota is not None:
            return RuntimeSpaceReservationResult(
                reservation=None,
                blocked_reason=f"runtime_space_quota_exceeded:{exceeded_quota.quota_key}",
            )

        for quota_key, amount in usage.items():
            quota = quotas.get(quota_key)
            if quota is not None:
                quota.reserved_value += amount

        if reservation is None:
            reservation = RuntimeSpaceReservation(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
                task_id=task_id,
                task_step_id=task_step_id,
                reservation_key=reservation_key,
                resource_usage=dict(usage),
            )
            self._session.add(reservation)
        else:
            reservation.task_id = task_id
            reservation.task_step_id = task_step_id
            reservation.agent_run_id = None
            reservation.resource_usage = dict(usage)
            reservation.status = "active"
            reservation.released_at = None
            reservation.expires_at = None

        self._append_event(
            runtime_space,
            "runtime_space.reserved",
            f"Reserved runtime space capacity for {reservation_key}",
            {
                "reservation_key": reservation_key,
                "resource_usage": dict(usage),
            },
        )
        self._session.flush([reservation, *quotas.values()])
        return RuntimeSpaceReservationResult(reservation=reservation)

    def attach_reservation_to_run(
        self,
        reservation: RuntimeSpaceReservation,
        agent_run_id: UUID,
    ) -> None:
        reservation.agent_run_id = agent_run_id
        self._session.flush([reservation])

    def active_reservation_usage_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
    ) -> dict[str, int]:
        usage: dict[str, int] = {}
        reservations = self._session.scalars(
            select(RuntimeSpaceReservation).where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.agent_run_id == agent_run_id,
                RuntimeSpaceReservation.status == "active",
            )
        ).all()
        for reservation in reservations:
            for quota_key, amount in self._reservation_usage(reservation).items():
                usage[quota_key] = usage.get(quota_key, 0) + amount
        return usage

    def release_reservations_for_run(
        self,
        *,
        workspace_id: UUID,
        agent_run_id: UUID,
        released_at: datetime | None = None,
    ) -> int:
        release_time = released_at or datetime.now(UTC)
        reservations = self._session.scalars(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.agent_run_id == agent_run_id,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        ).all()
        if not reservations:
            return 0

        quota_keys = {
            quota_key
            for reservation in reservations
            for quota_key in self._reservation_usage(reservation)
        }
        quotas = {
            (quota.runtime_space_id, quota.quota_key): quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id.in_(
                        {reservation.runtime_space_id for reservation in reservations}
                    ),
                    RuntimeSpaceQuota.quota_key.in_(quota_keys),
                )
                .with_for_update()
            ).all()
        }

        runtime_space_ids = {reservation.runtime_space_id for reservation in reservations}
        runtime_spaces = {
            runtime_space.id: runtime_space
            for runtime_space in self._session.scalars(
                select(RuntimeSpace).where(RuntimeSpace.id.in_(runtime_space_ids))
            ).all()
        }
        for reservation in reservations:
            for quota_key, amount in self._reservation_usage(reservation).items():
                quota = quotas.get((reservation.runtime_space_id, quota_key))
                if quota is not None:
                    quota.reserved_value = max(0, quota.reserved_value - amount)
            reservation.status = "released"
            reservation.released_at = release_time
            runtime_space = runtime_spaces.get(reservation.runtime_space_id)
            if runtime_space is not None:
                self._append_event(
                    runtime_space,
                    "runtime_space.reservation_released",
                    f"Released runtime space capacity for run {agent_run_id}",
                    {
                        "agent_run_id": str(agent_run_id),
                        "reservation_key": reservation.reservation_key,
                    },
                )
        self._session.flush([*reservations, *quotas.values()])
        return len(reservations)

    def release_reservation_by_key(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
        reservation_key: str,
        released_at: datetime | None = None,
    ) -> bool:
        release_time = released_at or datetime.now(UTC)
        reservation = self._session.scalar(
            select(RuntimeSpaceReservation)
            .where(
                RuntimeSpaceReservation.workspace_id == workspace_id,
                RuntimeSpaceReservation.runtime_space_id == runtime_space_id,
                RuntimeSpaceReservation.reservation_key == reservation_key,
                RuntimeSpaceReservation.status == "active",
            )
            .with_for_update()
        )
        if reservation is None:
            return False
        usage = self._reservation_usage(reservation)
        quotas = {
            quota.quota_key: quota
            for quota in self._session.scalars(
                select(RuntimeSpaceQuota)
                .where(
                    RuntimeSpaceQuota.workspace_id == workspace_id,
                    RuntimeSpaceQuota.runtime_space_id == runtime_space_id,
                    RuntimeSpaceQuota.quota_key.in_(usage),
                )
                .with_for_update()
            ).all()
        }
        for quota_key, amount in usage.items():
            quota = quotas.get(quota_key)
            if quota is not None:
                quota.reserved_value = max(0, quota.reserved_value - amount)
        reservation.status = "released"
        reservation.released_at = release_time
        runtime_space = self.get_runtime_space(workspace_id, runtime_space_id)
        if runtime_space is not None:
            self._append_event(
                runtime_space,
                "runtime_space.reservation_released",
                f"Released runtime space capacity for {reservation_key}",
                {
                    "reservation_key": reservation_key,
                    "resource_usage": dict(usage),
                },
            )
        self._session.flush([reservation, *quotas.values()])
        return True

    def _normalize_target_id(
        self,
        *,
        workspace_id: UUID,
        scope: str,
        target_id: UUID | None,
    ) -> UUID:
        if scope == "workspace":
            return workspace_id
        if target_id is None:
            raise ValueError("target_id is required for team and task runtime spaces")
        if scope == "team":
            exists = self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == target_id,
                    AgentTeam.status == "active",
                )
            )
            if exists is None:
                raise ValueError("Team not found")
            return target_id
        if scope == "task":
            exists = self._session.scalar(
                select(Task.id).where(Task.workspace_id == workspace_id, Task.id == target_id)
            )
            if exists is None:
                raise ValueError("Task not found")
            return target_id
        raise ValueError("Invalid runtime space scope")

    def _require_runtime_template(self, template_id: UUID) -> None:
        template = self._session.scalar(
            select(RuntimeTemplate.id).where(
                RuntimeTemplate.id == template_id,
                RuntimeTemplate.status == "active",
            )
        )
        if template is None:
            raise ValueError("Runtime template not found")

    def _bind_target(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        target_id: UUID,
    ) -> None:
        target_type = TARGET_TYPES_BY_SCOPE[runtime_space.scope]
        existing = self._session.scalar(
            select(RuntimeSpaceBinding).where(
                RuntimeSpaceBinding.workspace_id == workspace_id,
                RuntimeSpaceBinding.target_type == target_type,
                RuntimeSpaceBinding.target_id == target_id,
                RuntimeSpaceBinding.status == "active",
            )
        )
        if existing is not None:
            raise ValueError("Target already has an active runtime space")
        self._session.add(
            RuntimeSpaceBinding(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space.id,
                target_type=target_type,
                target_id=target_id,
            )
        )

    def _replace_quotas(
        self,
        *,
        workspace_id: UUID,
        runtime_space: RuntimeSpace,
        quota_limits: dict[str, int],
    ) -> None:
        existing = self._session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
            )
        ).all()
        existing_by_key = {quota.quota_key: quota for quota in existing}
        for quota_key, limit_value in quota_limits.items():
            quota = existing_by_key.pop(quota_key, None)
            if quota is None:
                quota = RuntimeSpaceQuota(
                    workspace_id=workspace_id,
                    runtime_space_id=runtime_space.id,
                    quota_key=quota_key,
                    limit_value=limit_value,
                )
                self._session.add(quota)
            else:
                quota.limit_value = limit_value
                quota.status = "active"
        for quota in existing_by_key.values():
            quota.status = "disabled"

    def _clear_runtime_space_pause_blocks(
        self,
        *,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> int:
        steps = self._session.scalars(
            select(TaskStep)
            .join(Task, Task.id == TaskStep.task_id)
            .where(
                TaskStep.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                TaskStep.status == "queued",
                (
                    (TaskStep.runtime_space_id == runtime_space_id)
                    | (
                        TaskStep.runtime_space_id.is_(None)
                        & (Task.runtime_space_id == runtime_space_id)
                    )
                ),
            )
        ).all()
        cleared = 0
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            if dependencies.get("scheduling_status") != "blocked":
                continue
            if dependencies.get("blocked_reason") != "runtime_space_paused":
                continue
            updated = dict(dependencies)
            updated.pop("scheduling_status", None)
            updated.pop("blocked_reason", None)
            updated.pop("priority_score", None)
            step.dependencies = updated
            cleared += 1
        return cleared

    def _append_event(
        self,
        runtime_space: RuntimeSpace,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime_space.workspace_id,
                runtime_space_id=runtime_space.id,
                event_type=event_type,
                message=message,
                event_metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _normalize_reservation_usage(
        self,
        resource_usage: dict[str, int] | None,
    ) -> dict[str, int]:
        usage = resource_usage or {RUN_CAPACITY_QUOTA_KEY: 1}
        normalized = {key: value for key, value in usage.items() if value > 0}
        return normalized or {RUN_CAPACITY_QUOTA_KEY: 1}

    def _reservation_usage(self, reservation: RuntimeSpaceReservation) -> dict[str, int]:
        usage: dict[str, int] = {}
        for quota_key, value in reservation.resource_usage.items():
            if isinstance(value, int) and value > 0:
                usage[quota_key] = value
        return usage

    def _first_exceeded_quota(
        self,
        quotas: dict[str, RuntimeSpaceQuota],
        usage: dict[str, int],
    ) -> RuntimeSpaceQuota | None:
        for quota_key, amount in usage.items():
            quota = quotas.get(quota_key)
            if quota is not None and quota.reserved_value + amount > quota.limit_value:
                return quota
        return None

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _non_empty_string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _quota_diagnostic(quota: RuntimeSpaceQuota) -> RuntimeSpaceQuotaDiagnosticResponse:
    utilization = (
        round(quota.reserved_value / quota.limit_value, 4) if quota.limit_value > 0 else 0.0
    )
    return RuntimeSpaceQuotaDiagnosticResponse(
        quota_key=quota.quota_key,
        limit_value=quota.limit_value,
        reserved_value=quota.reserved_value,
        unit=quota.unit,
        utilization=utilization,
        saturated=quota.limit_value > 0 and quota.reserved_value >= quota.limit_value,
    )


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _active_reservation_matches(
    *,
    reservation: RuntimeSpaceReservation,
    task_id: UUID | None,
    task_step_id: UUID | None,
    resource_usage: dict[str, int],
) -> bool:
    if reservation.task_id != task_id:
        return False
    if reservation.task_step_id != task_step_id:
        return False
    existing_usage = {
        key: value
        for key, value in reservation.resource_usage.items()
        if isinstance(value, int) and value > 0
    }
    return existing_usage == resource_usage
