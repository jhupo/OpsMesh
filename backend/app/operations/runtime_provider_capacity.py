from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operation_capacity import RuntimeProviderCapacityResponse
from backend.app.operations.utils import capacity_slots_from_metadata
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime


class RuntimeProviderCapacityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runtime_provider_capacity(
        self,
        workspace_id: UUID,
    ) -> list[RuntimeProviderCapacityResponse]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime)
            .where(WorkspaceRuntime.workspace_id == workspace_id)
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
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
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
