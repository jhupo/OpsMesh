from __future__ import annotations

from sqlalchemy import select

from backend.app.admin.base import AdminRedisService
from backend.app.admin.common import positive_int, top_counts
from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.approvals.models import Approval
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue import RedisQueue


class AdminOperationsSummaryService(AdminRedisService):
    def queue_metrics(self, queue_name: str) -> QueueMetricsResponse:
        if self._redis is None:
            return QueueMetricsResponse(
                queue_name=queue_name,
                queued=0,
                dead_letter=0,
                idempotency_keys=0,
            )
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return QueueMetricsResponse(
            queue_name=queue_name,
            queued=queue.count_queued(),
            dead_letter=queue.count_dead_letters(),
            idempotency_keys=self._count_keys(self._keys.idempotency_key("*", "*")),
        )

    def operations_summary(self, queue_name: str = "agent_runs") -> dict[str, object]:
        queue_metrics = self.queue_metrics(queue_name)
        return {
            "queue": {
                "queue_name": queue_name,
                "queued": queue_metrics.queued,
                "dead_letter": queue_metrics.dead_letter,
                "idempotency_keys": queue_metrics.idempotency_keys,
                "oldest_queued_at": self._oldest_queue_created_at(queue_name),
                "highest_priority": self._highest_queue_priority(queue_name),
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

    def list_dead_letters(self, queue_name: str, limit: int) -> tuple[list[JobPayload], int]:
        if self._redis is None:
            return [], 0
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.list_dead_letters(limit), queue.count_dead_letters()

    def requeue_dead_letter(self, queue_name: str, job_id) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=None)

    def _oldest_queue_created_at(self, queue_name: str) -> str | None:
        queued_jobs = self._queued_jobs(queue_name)
        if not queued_jobs:
            return None
        return min(job.created_at for job in queued_jobs).isoformat()

    def _highest_queue_priority(self, queue_name: str) -> int | None:
        queued_jobs = self._queued_jobs(queue_name)
        if not queued_jobs:
            return None
        return max(job.priority for job in queued_jobs)

    def _queued_jobs(self, queue_name: str, limit: int = 500) -> list[JobPayload]:
        if self._redis is None:
            return []
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.peek(limit=limit)

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
