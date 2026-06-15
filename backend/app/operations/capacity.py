from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import (
    OperationsCapacityResponse,
    OperationsRuntimeCapacityResponse,
    QueueLatencyResponse,
    RuntimeProviderCapacityResponse,
    RuntimeSpaceQuotaUsageResponse,
    RuntimeSpaceSaturationResponse,
    WorkerCapacityAggregateResponse,
    WorkerTypeCapacityResponse,
)
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.operations.utils import capacity_slots_from_metadata, positive_int
from backend.app.operations.workers import RUNNING_LEASE_STATUSES
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.workers.queue import RedisQueue


class OperationsCapacityService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def capacity_payload(self, workspace_id: UUID, queue_name: str) -> OperationsCapacityResponse:
        return OperationsCapacityResponse(
            generated_at=datetime.now(UTC),
            queue=self.queue_latency(queue_name, workspace_id),
            worker_capacity=self.worker_capacity_aggregate(),
            runtime_spaces=self.runtime_space_saturation(workspace_id),
        )

    def runtime_capacity_payload(self, workspace_id: UUID) -> OperationsRuntimeCapacityResponse:
        return OperationsRuntimeCapacityResponse(
            generated_at=datetime.now(UTC),
            providers=self.runtime_provider_capacity(workspace_id),
            worker_types=self.worker_type_capacity(),
            runtime_spaces=self.runtime_space_saturation(workspace_id),
        )

    def queue_latency(self, queue_name: str, workspace_id: UUID) -> QueueLatencyResponse:
        if self._redis is None:
            return _empty_queue_latency(queue_name)
        queue = RedisQueue(self._redis, self._keys, queue_name)
        jobs = [job for job in queue.peek(limit=500) if job.workspace_id == workspace_id]
        if not jobs:
            return _empty_queue_latency(queue_name)
        now = datetime.now(UTC)
        ages = [max(0, int((now - job.created_at).total_seconds())) for job in jobs]
        return QueueLatencyResponse(
            queue_name=queue_name,
            queued=len(jobs),
            oldest_age_seconds=max(ages),
            newest_age_seconds=min(ages),
            average_age_seconds=int(sum(ages) / len(ages)),
            highest_priority=max(job.priority for job in jobs),
        )

    def worker_capacity_aggregate(self) -> WorkerCapacityAggregateResponse:
        nodes = list(self._session.scalars(select(WorkerNode)).all())
        running_jobs = int(
            self._session.scalar(
                select(func.count())
                .select_from(WorkerLease)
                .where(
                    WorkerLease.status.in_(RUNNING_LEASE_STATUSES),
                )
            )
            or 0
        )
        max_jobs = sum(positive_int(node.capacity.get("max_jobs"), 1) for node in nodes)
        available_slots = max(0, max_jobs - running_jobs)
        online = sum(1 for node in nodes if node.status == "online")
        draining = sum(1 for node in nodes if node.status == "draining")
        offline = sum(1 for node in nodes if node.status == "offline")
        utilization = round(running_jobs / max_jobs, 4) if max_jobs > 0 else 0.0
        return WorkerCapacityAggregateResponse(
            workers_total=len(nodes),
            workers_online=online,
            workers_draining=draining,
            workers_offline=offline,
            max_jobs=max_jobs,
            running_jobs=running_jobs,
            available_slots=available_slots,
            utilization=utilization,
        )

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
        active_runtime_counts = dict(
            self._session.execute(
                select(WorkspaceRuntime.runtime_space_id, func.count())
                .where(
                    WorkspaceRuntime.workspace_id == workspace_id,
                    WorkspaceRuntime.runtime_space_id.in_(space_ids),
                    WorkspaceRuntime.status.in_(["created", "running"]),
                )
                .group_by(WorkspaceRuntime.runtime_space_id)
            ).all()
        )
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
        responses: list[RuntimeSpaceSaturationResponse] = []
        for space in spaces:
            quota_usages = [
                _runtime_space_quota_usage(quota) for quota in quotas_by_space.get(space.id, [])
            ]
            responses.append(
                RuntimeSpaceSaturationResponse(
                    runtime_space_id=space.id,
                    name=space.name,
                    status=space.status,
                    active_runtimes=int(active_runtime_counts.get(space.id, 0)),
                    quotas=quota_usages,
                    saturated=any(quota.saturated for quota in quota_usages),
                )
            )
        return responses

    def runtime_provider_capacity(
        self,
        workspace_id: UUID,
    ) -> list[RuntimeProviderCapacityResponse]:
        runtimes = self._session.scalars(
            select(WorkspaceRuntime)
            .where(WorkspaceRuntime.workspace_id == workspace_id)
            .order_by(WorkspaceRuntime.runtime_provider.asc(), WorkspaceRuntime.runtime_type.asc())
        ).all()
        active_runs_by_runtime = dict(
            self._session.execute(
                select(AgentRun.runtime_id, func.count())
                .where(
                    AgentRun.workspace_id == workspace_id,
                    AgentRun.runtime_id.is_not(None),
                    AgentRun.status.in_(["queued", "running", "waiting_approval"]),
                )
                .group_by(AgentRun.runtime_id)
            ).all()
        )
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for runtime in runtimes:
            key = (runtime.runtime_provider, runtime.runtime_type)
            bucket = grouped.setdefault(
                key,
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

    def worker_type_capacity(self) -> list[WorkerTypeCapacityResponse]:
        nodes = self._session.scalars(select(WorkerNode)).all()
        running_by_worker = dict(
            self._session.execute(
                select(WorkerLease.worker_id, func.count())
                .where(WorkerLease.status.in_(RUNNING_LEASE_STATUSES))
                .group_by(WorkerLease.worker_id)
            ).all()
        )
        grouped: dict[str, dict[str, int]] = {}
        for node in nodes:
            bucket = grouped.setdefault(
                node.worker_type,
                {
                    "workers_total": 0,
                    "workers_online": 0,
                    "workers_draining": 0,
                    "max_jobs": 0,
                    "running_jobs": 0,
                    "available_slots": 0,
                },
            )
            max_jobs = positive_int(node.capacity.get("max_jobs"), 1)
            running_jobs = int(running_by_worker.get(node.worker_id, 0))
            bucket["workers_total"] += 1
            bucket["workers_online"] += 1 if node.status == "online" else 0
            bucket["workers_draining"] += 1 if node.status == "draining" else 0
            bucket["max_jobs"] += max_jobs
            bucket["running_jobs"] += running_jobs
            bucket["available_slots"] += max(0, max_jobs - running_jobs)
        return [
            WorkerTypeCapacityResponse(
                worker_type=worker_type,
                utilization=round(values["running_jobs"] / values["max_jobs"], 4)
                if values["max_jobs"] > 0
                else 0.0,
                **values,
            )
            for worker_type, values in sorted(grouped.items())
        ]


def _empty_queue_latency(queue_name: str) -> QueueLatencyResponse:
    return QueueLatencyResponse(
        queue_name=queue_name,
        queued=0,
        oldest_age_seconds=None,
        newest_age_seconds=None,
        average_age_seconds=None,
        highest_priority=None,
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
