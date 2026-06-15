from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.core.metrics import GaugeMetric
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.operations.utils import (
    capacity_slots_from_metadata,
    ensure_aware_utc,
    non_negative_int,
)
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpaceQuota
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.teams.models import AgentTeam
from backend.app.teams.runtime import (
    TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS,
    TEAM_RUNTIME_STATUS_KEY,
    TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY,
)
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue

PROMETHEUS_WORKER_STALE_AFTER_SECONDS = 300
PROMETHEUS_QUEUE_SCAN_LIMIT = 1_000
ACTIVE_RUNTIME_RUN_STATUSES = {"queued", "running", "waiting_approval"}


class OperationsObservabilityService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None,
        key_builder: RedisKeyBuilder,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder

    def queue_metrics(
        self,
        queue_name: str,
        workspace_id: UUID | None = None,
    ) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        queued = queue.count_queued(workspace_id=workspace_id)
        dead = queue.count_dead_letters(workspace_id=workspace_id)
        idempotency_pattern = (
            self._keys.idempotency_key(str(workspace_id), "*")
            if workspace_id is not None
            else self._keys.idempotency_key("*", "*")
        )
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queued,
            dead_letter=dead,
            idempotency_keys=self._count_keys(idempotency_pattern),
        )

    def prometheus_gauges(
        self,
        queue_name: str,
        *,
        worker_stale_after_seconds: int = PROMETHEUS_WORKER_STALE_AFTER_SECONDS,
        queue_scan_limit: int = PROMETHEUS_QUEUE_SCAN_LIMIT,
    ) -> list[GaugeMetric]:
        now = datetime.now(UTC)
        gauges: list[GaugeMetric] = []

        try:
            queue = self.queue_metrics(queue_name)
            gauges.extend(
                [
                    GaugeMetric(
                        "opsmesh_queue_jobs",
                        queue.queued,
                        labels={"queue_name": queue_name, "state": "queued"},
                        help_text="Jobs currently waiting in Redis queues.",
                    ),
                    GaugeMetric(
                        "opsmesh_queue_jobs",
                        queue.dead_letter,
                        labels={"queue_name": queue_name, "state": "dead_letter"},
                        help_text="Jobs currently waiting in Redis queues.",
                    ),
                    GaugeMetric(
                        "opsmesh_queue_idempotency_keys",
                        queue.idempotency_keys,
                        labels={"queue_name": queue_name},
                        help_text="Active Redis idempotency keys for queued work.",
                    ),
                    GaugeMetric(
                        "opsmesh_queue_oldest_queued_age_seconds",
                        self._oldest_queued_age_seconds(queue_name, now, queue_scan_limit) or 0,
                        labels={"queue_name": queue_name},
                        help_text="Age of the oldest queued job seen in the queue scan.",
                    ),
                ]
            )
        except (OSError, RedisError, TimeoutError):
            pass

        try:
            gauges.extend(self._worker_gauges(now, worker_stale_after_seconds))
            gauges.extend(self._runtime_gauges())
            gauges.extend(self._team_runtime_gauges(now))
        except SQLAlchemyError:
            pass
        return gauges

    def _count_keys(self, pattern: str) -> int:
        if self._redis is None:
            return 0
        return sum(1 for _ in self._redis.scan_iter(pattern))

    def _oldest_queued_age_seconds(
        self,
        queue_name: str,
        now: datetime,
        scan_limit: int,
    ) -> int | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return _oldest_job_age(now, queue.peek(limit=scan_limit))

    def _worker_gauges(
        self,
        now: datetime,
        stale_after_seconds: int,
    ) -> list[GaugeMetric]:
        stale_cutoff = now - timedelta(seconds=stale_after_seconds)
        worker_states = {"online": 0, "offline": 0, "stale": 0}
        nodes = self._session.scalars(select(WorkerNode)).all()
        for node in nodes:
            if ensure_aware_utc(node.last_seen_at) < stale_cutoff:
                worker_states["stale"] += 1
            elif node.status == "online":
                worker_states["online"] += 1
            elif node.status == "offline":
                worker_states["offline"] += 1

        gauges = [
            GaugeMetric(
                "opsmesh_workers",
                count,
                labels={"state": state},
                help_text="Worker nodes by operational state.",
            )
            for state, count in sorted(worker_states.items())
        ]

        lease_counts = {
            status: int(count)
            for status, count in self._session.execute(
                select(WorkerLease.status, func.count())
                .where(WorkerLease.status.in_(["running", "failed"]))
                .group_by(WorkerLease.status)
            ).all()
        }
        gauges.extend(
            GaugeMetric(
                "opsmesh_worker_leases",
                lease_counts.get(status, 0),
                labels={"status": status},
                help_text="Worker leases by lifecycle status.",
            )
            for status in ("running", "failed")
        )
        return gauges

    def _runtime_gauges(self) -> list[GaugeMetric]:
        gauges: list[GaugeMetric] = []
        active_runs_by_runtime = dict(
            self._session.execute(
                select(AgentRun.runtime_id, func.count())
                .where(
                    AgentRun.runtime_id.is_not(None),
                    AgentRun.status.in_(ACTIVE_RUNTIME_RUN_STATUSES),
                )
                .group_by(AgentRun.runtime_id)
            ).all()
        )
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for runtime in self._session.scalars(select(WorkspaceRuntime)).all():
            key = (runtime.runtime_provider, runtime.runtime_type)
            bucket = grouped.setdefault(key, {"capacity_slots": 0, "active_runs": 0})
            bucket["capacity_slots"] += capacity_slots_from_metadata(runtime.capabilities)
            bucket["active_runs"] += int(active_runs_by_runtime.get(runtime.id, 0))

        for (provider, runtime_type), values in sorted(grouped.items()):
            labels = {"provider": provider, "runtime_type": runtime_type}
            capacity_slots = values["capacity_slots"]
            active_runs = values["active_runs"]
            saturation = active_runs / capacity_slots if capacity_slots > 0 else 0.0
            gauges.extend(
                [
                    GaugeMetric(
                        "opsmesh_runtime_capacity_slots",
                        capacity_slots,
                        labels=labels,
                        help_text="Runtime capacity slots by provider and runtime type.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_active_runs",
                        active_runs,
                        labels=labels,
                        help_text="Active runs assigned to runtimes by provider and type.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_saturation_ratio",
                        round(saturation, 4),
                        labels=labels,
                        help_text="Runtime active-run saturation by provider and type.",
                    ),
                ]
            )

        quota_rows = self._session.execute(
            select(
                RuntimeSpaceQuota.quota_key,
                RuntimeSpaceQuota.unit,
                func.sum(RuntimeSpaceQuota.reserved_value),
                func.sum(RuntimeSpaceQuota.limit_value),
            )
            .where(RuntimeSpaceQuota.status == "active")
            .group_by(RuntimeSpaceQuota.quota_key, RuntimeSpaceQuota.unit)
        ).all()
        for quota_key, unit, reserved, limit in sorted(quota_rows):
            labels = {"quota_key": quota_key, "unit": unit}
            reserved_value = int(reserved or 0)
            limit_value = int(limit or 0)
            usage = reserved_value / limit_value if limit_value > 0 else 0.0
            gauges.extend(
                [
                    GaugeMetric(
                        "opsmesh_runtime_space_quota_reserved",
                        reserved_value,
                        labels=labels,
                        help_text="Reserved runtime space quota by quota key.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_space_quota_limit",
                        limit_value,
                        labels=labels,
                        help_text="Configured runtime space quota limit by quota key.",
                    ),
                    GaugeMetric(
                        "opsmesh_runtime_space_quota_usage_ratio",
                        round(usage, 4),
                        labels=labels,
                        help_text="Runtime space quota usage ratio by quota key.",
                    ),
                ]
            )
        return gauges

    def _team_runtime_gauges(self, now: datetime) -> list[GaugeMetric]:
        teams = self._session.scalars(select(AgentTeam).where(AgentTeam.status == "active")).all()
        runtime_ids = {
            runtime_id
            for team in teams
            for runtime_id in [_team_runtime_workspace_runtime_id(team)]
            if runtime_id is not None
        }
        runtimes = (
            {
                runtime.id: runtime
                for runtime in self._session.scalars(
                    select(WorkspaceRuntime).where(WorkspaceRuntime.id.in_(runtime_ids))
                ).all()
            }
            if runtime_ids
            else {}
        )
        health_counts = {
            "starting": 0,
            "healthy": 0,
            "stale": 0,
            "degraded": 0,
            "paused": 0,
            "stopped": 0,
        }
        iteration_count = 0
        scheduled_loop_enabled = 0
        for team in teams:
            runtime_metadata = _team_runtime_metadata(team)
            if not runtime_metadata:
                continue
            runtime_id = _team_runtime_workspace_runtime_id(team)
            health = _team_runtime_health_for_metrics(
                runtime_metadata=runtime_metadata,
                runtime=runtimes.get(runtime_id) if runtime_id is not None else None,
                generated_at=now,
            )
            health_counts[health] = health_counts.get(health, 0) + 1
            iteration_count += non_negative_int(runtime_metadata.get("iteration_count"))
            scheduling_policy = runtime_metadata.get("scheduling_policy")
            if not isinstance(scheduling_policy, dict) or (
                scheduling_policy.get("scheduled_loop_enabled") is not False
            ):
                scheduled_loop_enabled += 1
        gauges = [
            GaugeMetric(
                "opsmesh_team_runtimes",
                count,
                labels={"health": health},
                help_text="Team runtimes by low-cardinality runtime health.",
            )
            for health, count in sorted(health_counts.items())
        ]
        gauges.append(
            GaugeMetric(
                "opsmesh_team_runtime_iterations_total",
                iteration_count,
                labels={},
                help_text="Total persisted team runtime iterations across active teams.",
            )
        )
        gauges.append(
            GaugeMetric(
                "opsmesh_team_runtime_scheduled_loops",
                scheduled_loop_enabled,
                labels={"state": "enabled"},
                help_text="Active team runtimes with scheduled loop cadence enabled.",
            )
        )
        return gauges


def _oldest_job_age(now: datetime, jobs: list[JobPayload]) -> int | None:
    ages = [
        max(0, int((ensure_aware_utc(now) - ensure_aware_utc(job.created_at)).total_seconds()))
        for job in jobs
    ]
    return max(ages) if ages else None


def _team_runtime_metadata(team: AgentTeam) -> dict[str, object]:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime_metadata = policy.get(TEAM_RUNTIME_STATUS_KEY)
    return dict(runtime_metadata) if isinstance(runtime_metadata, dict) else {}


def _team_runtime_workspace_runtime_id(team: AgentTeam) -> UUID | None:
    runtime_id = _team_runtime_metadata(team).get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
    if isinstance(runtime_id, UUID):
        return runtime_id
    if isinstance(runtime_id, str) and runtime_id:
        try:
            return UUID(runtime_id)
        except ValueError:
            return None
    return None


def _team_runtime_health_for_metrics(
    *,
    runtime_metadata: dict[str, object],
    runtime: WorkspaceRuntime | None,
    generated_at: datetime,
) -> str:
    status = runtime_metadata.get("status")
    if status == "paused":
        return "paused"
    if status == "stopped" or status is None:
        return "stopped"
    if runtime is None and runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY):
        return "degraded"
    if runtime is not None and (
        runtime.status != "running" or runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if runtime_metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    last_heartbeat_at = _datetime_from_metadata(runtime_metadata.get("last_heartbeat_at"))
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    return "healthy"


def _datetime_from_metadata(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return ensure_aware_utc(parsed)
