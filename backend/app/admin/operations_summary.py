from __future__ import annotations

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.base import AdminRedisService
from backend.app.admin.common import positive_int, top_counts
from backend.app.admin.queue_operations import AdminQueueOperationsService
from backend.app.approvals.models import Approval
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task


class AdminOperationsSummaryService(AdminRedisService):
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
        *,
        queue_operations: AdminQueueOperationsService | None = None,
    ) -> None:
        super().__init__(session, redis, key_builder)
        self._queue_operations = queue_operations or AdminQueueOperationsService(
            self._session,
            self._redis,
            self._keys,
        )

    def operations_summary(self, queue_name: str = "agent_runs") -> dict[str, object]:
        queue_metrics = self._queue_operations.queue_metrics(queue_name)
        return {
            "queue": {
                "queue_name": queue_name,
                "queued": queue_metrics.queued,
                "dead_letter": queue_metrics.dead_letter,
                "idempotency_keys": queue_metrics.idempotency_keys,
                "oldest_queued_at": self._queue_operations.oldest_queued_at(queue_name),
                "highest_priority": self._queue_operations.highest_queue_priority(queue_name),
            },
            "workers": self._worker_capacity_summary(),
            "runtime_spaces": self._runtime_space_usage_summary(),
            "approvals": {
                "pending": self._count(select(Approval).where(Approval.status == "pending")),
                "runs_waiting": self._count(
                    select(AgentRun).where(AgentRun.status == "waiting_approval")
                ),
                "tasks_waiting": self._count(
                    select(Task).where(Task.status == "waiting_approval")
                ),
            },
            "failures": {
                "failed_runs": self._count(select(AgentRun).where(AgentRun.status == "failed")),
                "failed_worker_leases": self._count(
                    select(WorkerLease).where(WorkerLease.status == "failed")
                ),
                "top_run_error_codes": self._top_run_error_codes(),
                "top_security_reasons": self._top_security_reasons(),
            },
        }

    def _worker_capacity_summary(self) -> dict[str, object]:
        workers = self._session.scalars(select(WorkerNode)).all()
        active_leases = self._session.scalars(
            select(WorkerLease).where(WorkerLease.status == "running")
        ).all()
        running_by_worker: dict[str, int] = {}
        for lease in active_leases:
            running_by_worker[lease.worker_id] = running_by_worker.get(lease.worker_id, 0) + 1
        total_capacity = 0
        available_capacity = 0
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for worker in workers:
            by_status[worker.status] = by_status.get(worker.status, 0) + 1
            by_type[worker.worker_type] = by_type.get(worker.worker_type, 0) + 1
            max_jobs = positive_int(worker.capacity.get("max_jobs"), 1)
            total_capacity += max_jobs
            if worker.status == "online":
                available_capacity += max(0, max_jobs - running_by_worker.get(worker.worker_id, 0))
        return {
            "total": len(workers),
            "online": by_status.get("online", 0),
            "draining": by_status.get("draining", 0),
            "offline": by_status.get("offline", 0),
            "by_status": by_status,
            "by_type": by_type,
            "running_leases": len(active_leases),
            "total_capacity": total_capacity,
            "available_capacity": available_capacity,
        }

    def _runtime_space_usage_summary(self) -> dict[str, object]:
        quotas = self._session.scalars(
            select(RuntimeSpaceQuota).where(RuntimeSpaceQuota.status == "active")
        ).all()
        quota_usage: dict[str, dict[str, object]] = {}
        for quota in quotas:
            entry = quota_usage.setdefault(
                quota.quota_key,
                {
                    "limit_value": 0,
                    "reserved_value": 0,
                    "unit": quota.unit,
                    "max_utilization": 0.0,
                },
            )
            entry["limit_value"] = int(entry["limit_value"]) + quota.limit_value
            entry["reserved_value"] = int(entry["reserved_value"]) + quota.reserved_value
            utilization = (
                quota.reserved_value / quota.limit_value if quota.limit_value > 0 else 0.0
            )
            entry["max_utilization"] = max(float(entry["max_utilization"]), utilization)
        return {
            "total": self._count(select(RuntimeSpace)),
            "active": self._count(select(RuntimeSpace).where(RuntimeSpace.status == "active")),
            "quarantined": self._count(
                select(RuntimeSpace).where(RuntimeSpace.status == "quarantined")
            ),
            "quota_usage": quota_usage,
        }

    def _top_run_error_codes(self, limit: int = 5) -> list[dict[str, object]]:
        rows = self._session.scalars(
            select(AgentRun.error).where(
                AgentRun.status == "failed",
                AgentRun.error.is_not(None),
            )
        ).all()
        counts: dict[str, int] = {}
        for error in rows:
            if not isinstance(error, dict):
                continue
            code = error.get("code")
            if not isinstance(code, str) or not code:
                code = "unknown"
            counts[code] = counts.get(code, 0) + 1
        return top_counts(counts, limit)

    def _top_security_reasons(self, limit: int = 5) -> list[dict[str, object]]:
        rows = self._session.scalars(select(SecurityEvent.reason)).all()
        counts: dict[str, int] = {}
        for reason in rows:
            key = reason if reason else "unknown"
            counts[key] = counts.get(key, 0) + 1
        return top_counts(counts, limit)
