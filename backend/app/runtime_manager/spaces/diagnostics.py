from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.runtime_spaces import (
    RuntimeSpaceBlockedStepDiagnosticResponse,
    RuntimeSpaceDiagnosticsResponse,
    RuntimeSpaceQuotaDiagnosticResponse,
    RuntimeSpaceReservationDiagnosticResponse,
    RuntimeSpaceResponse,
    RuntimeSpaceRuntimeDiagnosticResponse,
)
from backend.app.core.typing import string_list
from backend.app.orchestration.policies.blocked_reasons import explain_blocked_reason
from backend.app.runtime_manager.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.tasks.models import Task, TaskStep


class RuntimeSpaceDiagnosticsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def diagnostics(
        self,
        workspace_id: UUID,
        runtime_space_id: UUID,
    ) -> RuntimeSpaceDiagnosticsResponse | None:
        runtime_space = self._session.scalar(
            select(RuntimeSpace).where(
                RuntimeSpace.workspace_id == workspace_id,
                RuntimeSpace.id == runtime_space_id,
            )
        )
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
        return RuntimeSpaceDiagnosticsResponse(
            runtime_space=RuntimeSpaceResponse.model_validate(runtime_space),
            quotas=[quota_diagnostic(quota) for quota in quotas],
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
                    policy_resolution=_policy_resolution(runtime),
                    last_heartbeat_at=runtime.last_heartbeat_at,
                )
                for runtime in runtimes
            ],
            blocked_steps=self.blocked_steps_for_runtime_space(
                workspace_id=workspace_id,
                runtime_space_id=runtime_space_id,
            ),
        )

    def blocked_steps_for_runtime_space(
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
                    blocked_resource_keys=string_list(dependencies.get("blocked_resource_keys")),
                    priority_score=_positive_int_or_none(dependencies.get("priority_score")),
                    created_at=step.created_at,
                )
            )
        return blocked_steps


def quota_diagnostic(quota: RuntimeSpaceQuota) -> RuntimeSpaceQuotaDiagnosticResponse:
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


def _policy_resolution(runtime: WorkspaceRuntime) -> dict[str, object]:
    capabilities = runtime.capabilities if isinstance(runtime.capabilities, dict) else {}
    policy_resolution = capabilities.get("policy_resolution")
    return policy_resolution if isinstance(policy_resolution, dict) else {}
