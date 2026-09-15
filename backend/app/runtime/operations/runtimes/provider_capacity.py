from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.utils import positive_int_or_none
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.domains.orchestration.workflows.statuses import (
    CAPACITY_CONSUMING_RUN_STATUS_VALUES,
)
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.operations.contracts.capacity import RuntimeProviderCapacityResponse

RUNTIME_CAPACITY_KEYS = ("max_concurrent_jobs", "max_jobs", "slots", "capacity_slots")


def capacity_slots_from_metadata(capabilities: Mapping[str, object]) -> int:
    for key in RUNTIME_CAPACITY_KEYS:
        value = positive_int_or_none(capabilities.get(key))
        if value is not None:
            return value
    return 1


class RuntimeProviderCapacityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runtime_provider_capacity(
        self,
        workspace_id: UUID,
    ) -> list[RuntimeProviderCapacityResponse]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime)
            .where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.execution_run_id.is_(None),
            )
            .order_by(WorkspaceRuntime.runtime_provider.asc(), WorkspaceRuntime.runtime_type.asc())
        ).all()
        active_runs_by_runtime = self._active_runs_by_runtime(workspace_id)
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for runtime in runtimes:
            bucket = grouped.setdefault(
                (runtime.runtime_provider, runtime.runtime_type),
                {
                    "total": 0,
                    "online": 0,
                    "offline": 0,
                    "degraded": 0,
                    "running": 0,
                    "capacity_slots": 0,
                    "active_runs": 0,
                },
            )
            _add_runtime_capacity(bucket, runtime, active_runs_by_runtime)
        return [
            RuntimeProviderCapacityResponse(
                provider=provider,
                runtime_type=runtime_type,
                utilization=round(values["active_runs"] / values["capacity_slots"], 4)
                if values["capacity_slots"] > 0
                else 0.0,
                **values,
            )
            for (provider, runtime_type), values in sorted(grouped.items())
        ]

    def _active_runs_by_runtime(self, workspace_id: UUID) -> dict[UUID, int]:
        return {
            runtime_id: int(count)
            for runtime_id, count in self._session.execute(
                select(AgentRun.runtime_id, func.count())
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.runtime_id.is_not(None),
                    AgentRun.status.in_(CAPACITY_CONSUMING_RUN_STATUS_VALUES),
                )
                .group_by(AgentRun.runtime_id)
            ).all()
        }


def _add_runtime_capacity(
    bucket: dict[str, int],
    runtime: WorkspaceRuntime,
    active_runs_by_runtime: dict[UUID, int],
) -> None:
    bucket["total"] += 1
    if runtime.connection_status == "online":
        bucket["online"] += 1
    elif runtime.connection_status == "degraded":
        bucket["degraded"] += 1
    elif runtime.connection_status == "offline":
        bucket["offline"] += 1
    if runtime.status in {"created", "running", "active"}:
        bucket["running"] += 1
    bucket["capacity_slots"] += capacity_slots_from_metadata(runtime.capabilities)
    bucket["active_runs"] += int(active_runs_by_runtime.get(runtime.id, 0))
