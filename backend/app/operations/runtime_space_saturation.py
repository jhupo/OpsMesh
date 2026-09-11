from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_capacity import (
    RuntimeSpaceQuotaUsageResponse,
    RuntimeSpaceSaturationResponse,
)
from backend.app.runtime_manager.models import WorkspaceRuntime
from backend.app.runtime_manager.spaces.models import RuntimeSpace, RuntimeSpaceQuota


class RuntimeSpaceSaturationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runtime_space_saturation(
        self,
        workspace_id: UUID,
    ) -> list[RuntimeSpaceSaturationResponse]:
        spaces = self._session.scalars(
            select(RuntimeSpace)
            .where(RuntimeSpace.workspace_id == workspace_id)
            .order_by(RuntimeSpace.created_at.desc())
        ).all()
        if not spaces:
            return []

        space_ids = [space.id for space in spaces]
        active_runtime_counts = self._active_runtime_counts(workspace_id, space_ids)
        quotas_by_space = self._active_quotas_by_space(workspace_id, space_ids)
        return [
            _runtime_space_response(
                space,
                active_runtimes=int(active_runtime_counts.get(space.id, 0)),
                quotas=quotas_by_space.get(space.id, []),
            )
            for space in spaces
        ]

    def _active_runtime_counts(
        self,
        workspace_id: UUID,
        space_ids: list[UUID],
    ) -> dict[UUID, int]:
        return {
            runtime_space_id: int(count)
            for runtime_space_id, count in self._session.execute(
                select(WorkspaceRuntime.runtime_space_id, func.count())
                .where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.runtime_space_id.in_(space_ids),
                    WorkspaceRuntime.status.in_(["created", "running"]),
                    WorkspaceRuntime.execution_run_id.is_(None),
                )
                .group_by(WorkspaceRuntime.runtime_space_id)
            ).all()
        }

    def _active_quotas_by_space(
        self,
        workspace_id: UUID,
        space_ids: list[UUID],
    ) -> dict[UUID, list[RuntimeSpaceQuota]]:
        quotas = self._session.scalars(
            select(RuntimeSpaceQuota).where(
                RuntimeSpaceQuota.workspace_id == workspace_id,
                RuntimeSpaceQuota.runtime_space_id.in_(space_ids),
                RuntimeSpaceQuota.status == "active",
            )
        ).all()
        quotas_by_space: dict[UUID, list[RuntimeSpaceQuota]] = {}
        for quota in quotas:
            quotas_by_space.setdefault(quota.runtime_space_id, []).append(quota)
        return quotas_by_space


def _runtime_space_response(
    space: RuntimeSpace,
    *,
    active_runtimes: int,
    quotas: list[RuntimeSpaceQuota],
) -> RuntimeSpaceSaturationResponse:
    quota_usages = [_runtime_space_quota_usage(quota) for quota in quotas]
    return RuntimeSpaceSaturationResponse(
        runtime_space_id=space.id,
        name=space.name,
        status=space.status,
        active_runtimes=active_runtimes,
        quotas=quota_usages,
        saturated=any(quota.saturated for quota in quota_usages),
    )


def _runtime_space_quota_usage(quota: RuntimeSpaceQuota) -> RuntimeSpaceQuotaUsageResponse:
    utilization = (
        round(quota.reserved_value / quota.limit_value, 4) if quota.limit_value > 0 else 0.0
    )
    return RuntimeSpaceQuotaUsageResponse(
        quota_key=quota.quota_key,
        limit_value=quota.limit_value,
        reserved_value=quota.reserved_value,
        unit=quota.unit,
        utilization=utilization,
        saturated=quota.limit_value > 0 and quota.reserved_value >= quota.limit_value,
    )
