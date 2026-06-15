from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from redis import Redis
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.admin.policies import (
    RISKY_EXECUTION_POLICY_KEY,
    WORKER_CONTROL_POLICY_KEY,
    WorkerControlPolicy,
    default_risky_execution_policy_value,
    default_worker_control_policy_value,
    normalize_risky_execution_policy_value,
    normalize_worker_control_policy_value,
)
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.operations import QueueMetricsResponse
from backend.app.approvals.models import Approval
from backend.app.core.errors import PolicyDeniedError
from backend.app.db.pagination import page_scalars
from backend.app.operations.models import WorkerLease, WorkerNode
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent, RuntimeSpaceQuota
from backend.app.runtimes.models import RuntimeEvent, RuntimeLease, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace

T = TypeVar("T")


class AdminControlPlaneService:
    def __init__(
        self,
        session: Session,
        redis: Redis[str] | None = None,
        key_builder: RedisKeyBuilder | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._keys = key_builder or RedisKeyBuilder("opsmesh")

    def overview(self) -> dict[str, int]:
        return {
            "workspaces_total": self._count(select(Workspace)),
            "workspaces_active": self._count(select(Workspace).where(Workspace.status == "active")),
            "workers_total": self._count(select(WorkerNode)),
            "workers_online": self._count(select(WorkerNode).where(WorkerNode.status == "online")),
            "workers_draining": self._count(
                select(WorkerNode).where(WorkerNode.status == "draining")
            ),
            "active_worker_leases": self._count(
                select(WorkerLease).where(WorkerLease.status == "running")
            ),
            "runtime_spaces_total": self._count(select(RuntimeSpace)),
            "runtime_spaces_quarantined": self._count(
                select(RuntimeSpace).where(RuntimeSpace.status == "quarantined")
            ),
            "runtimes_running": self._count(
                select(WorkspaceRuntime).where(WorkspaceRuntime.status == "running")
            ),
            "runtimes_offline": self._count(
                select(WorkspaceRuntime).where(
                    WorkspaceRuntime.connection_status == "offline",
                    WorkspaceRuntime.status != "deleted",
                )
            ),
            "critical_security_events": self._count(
                select(SecurityEvent).where(SecurityEvent.severity == "critical")
            ),
        }

    def list_workspaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[Workspace], int]:
        statement = select(Workspace)
        if status is not None:
            statement = statement.where(Workspace.status == status)
        return self._page(statement.order_by(Workspace.created_at.desc()), page)

    def list_workers(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        worker_type: str | None = None,
    ) -> tuple[list[WorkerNode], int]:
        statement = select(WorkerNode)
        if status is not None:
            statement = statement.where(WorkerNode.status == status)
        if worker_type is not None:
            statement = statement.where(WorkerNode.worker_type == worker_type)
        return self._page(statement.order_by(WorkerNode.last_seen_at.desc()), page)

    def drain_worker(self, worker_id: str) -> WorkerNode | None:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        if node is None:
            return None
        node.status = "draining"
        node.drain_requested_at = datetime.now(UTC)
        self._append_worker_control_event(
            "worker.drained",
            f"Worker {worker_id} marked draining",
            {"worker_id": worker_id, "reason": "Drain requested by platform admin"},
        )
        self._session.commit()
        self._session.refresh(node)
        return node

    def update_worker(
        self,
        worker_id: str,
        *,
        status: str | None,
        worker_type: str | None,
        queue_name: str | None,
        worker_version: str | None,
        hostname: str | None,
        capacity: dict[str, object] | None,
        details: dict[str, object] | None,
        reason: str,
        updated_by: str | None,
    ) -> WorkerNode | None:
        node = self._session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))
        if node is None:
            return None
        policy = self._worker_control_policy()
        self._assert_worker_update_allowed(
            policy,
            status=status,
            worker_type=worker_type,
            queue_name=queue_name,
            capacity=capacity,
        )
        before = _worker_node_snapshot(node)
        changed_fields: list[str] = []
        if status is not None:
            node.status = status
            node.drain_requested_at = datetime.now(UTC) if status == "draining" else None
            changed_fields.append("status")
        if worker_type is not None:
            node.worker_type = worker_type
            changed_fields.append("worker_type")
        if queue_name is not None:
            node.queue_name = queue_name
            changed_fields.append("queue_name")
        if worker_version is not None:
            node.worker_version = worker_version
            changed_fields.append("worker_version")
        if hostname is not None:
            node.hostname = hostname
            changed_fields.append("hostname")
        if capacity is not None:
            node.capacity = _normalized_worker_capacity(capacity, node.worker_type)
            changed_fields.append("capacity")
        elif worker_type is not None:
            node.capacity = _normalized_worker_capacity(node.capacity, node.worker_type)
        if details is not None:
            node.details = dict(details)
            changed_fields.append("details")
        if changed_fields:
            self._append_worker_control_event(
                "worker.updated",
                f"Worker {worker_id} updated",
                {
                    "worker_id": worker_id,
                    "changed_fields": changed_fields,
                    "before": before,
                    "after": _worker_node_snapshot(node),
                    "reason": reason,
                    "updated_by": updated_by,
                },
            )
        self._session.commit()
        self._session.refresh(node)
        return node

    def list_runtime_spaces(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[RuntimeSpace], int]:
        statement = select(RuntimeSpace)
        if status is not None:
            statement = statement.where(RuntimeSpace.status == status)
        if workspace_id is not None:
            statement = statement.where(RuntimeSpace.workspace_id == workspace_id)
        return self._page(statement.order_by(RuntimeSpace.created_at.desc()), page)

    def quarantine_runtime_space(
        self,
        runtime_space_id: UUID,
        *,
        reason: str,
    ) -> RuntimeSpace | None:
        runtime_space = self._session.get(RuntimeSpace, runtime_space_id)
        if runtime_space is None:
            return None
        runtime_space.status = "quarantined"
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime_space.workspace_id,
                runtime_space_id=runtime_space.id,
                event_type="runtime_space.quarantined",
                message=reason,
                event_metadata={"source": "platform_admin"},
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        self._session.refresh(runtime_space)
        return runtime_space

    def list_worker_leases(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
        worker_id: str | None = None,
    ) -> tuple[list[WorkerLease], int]:
        statement = select(WorkerLease)
        if status is not None:
            statement = statement.where(WorkerLease.status == status)
        if workspace_id is not None:
            statement = statement.where(WorkerLease.workspace_id == workspace_id)
        if worker_id is not None:
            statement = statement.where(WorkerLease.worker_id == worker_id)
        return self._page(statement.order_by(WorkerLease.created_at.desc()), page)

    def list_runtime_leases(
        self,
        page: PageParams,
        *,
        status: str | None = None,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        workspace_runtime_id: UUID | None = None,
    ) -> tuple[list[RuntimeLease], int]:
        statement = select(RuntimeLease)
        if status is not None:
            statement = statement.where(RuntimeLease.status == status)
        if workspace_id is not None:
            statement = statement.where(RuntimeLease.workspace_id == workspace_id)
        if runtime_space_id is not None:
            statement = statement.where(RuntimeLease.runtime_space_id == runtime_space_id)
        if workspace_runtime_id is not None:
            statement = statement.where(RuntimeLease.workspace_runtime_id == workspace_runtime_id)
        return self._page(statement.order_by(RuntimeLease.created_at.desc()), page)

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
        worker_capacity = self._worker_capacity_summary()
        runtime_space_usage = self._runtime_space_usage_summary()
        return {
            "queue": {
                "queue_name": queue_name,
                "queued": queue_metrics.queued,
                "dead_letter": queue_metrics.dead_letter,
                "idempotency_keys": queue_metrics.idempotency_keys,
                "oldest_queued_at": self._oldest_queue_created_at(queue_name),
                "highest_priority": self._highest_queue_priority(queue_name),
            },
            "workers": worker_capacity,
            "runtime_spaces": runtime_space_usage,
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

    def requeue_dead_letter(self, queue_name: str, job_id: UUID) -> JobPayload | None:
        if self._redis is None:
            return None
        queue = RedisQueue(self._redis, self._keys, queue_name)
        return queue.requeue_dead_letter(job_id, workspace_id=None)

    def list_runtimes(
        self,
        page: PageParams,
        *,
        workspace_id: UUID | None = None,
        runtime_space_id: UUID | None = None,
        status: str | None = None,
        connection_status: str | None = None,
    ) -> tuple[list[WorkspaceRuntime], int]:
        statement = select(WorkspaceRuntime).where(WorkspaceRuntime.status != "deleted")
        if workspace_id is not None:
            statement = statement.where(WorkspaceRuntime.workspace_id == workspace_id)
        if runtime_space_id is not None:
            statement = statement.where(WorkspaceRuntime.runtime_space_id == runtime_space_id)
        if status is not None:
            statement = statement.where(WorkspaceRuntime.status == status)
        if connection_status is not None:
            statement = statement.where(WorkspaceRuntime.connection_status == connection_status)
        return self._page(statement.order_by(WorkspaceRuntime.created_at.desc()), page)

    def force_stop_runtime(
        self,
        runtime_id: UUID,
        *,
        reason: str,
        queue: RedisQueue | None = None,
    ) -> WorkspaceRuntime | None:
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )
        if runtime is None:
            return None
        now = datetime.now(UTC)
        runtime.status = "stopping"
        runtime.connection_status = "degraded"
        lease = self._session.scalar(
            select(RuntimeLease).where(RuntimeLease.workspace_runtime_id == runtime.id)
        )
        if lease is not None and lease.status in {"running", "acquired"}:
            lease.lease_metadata = {
                **lease.lease_metadata,
                "stop_requested_by": "platform_admin",
                "stop_request_reason": reason,
                "stop_requested_at": now.isoformat(),
            }
        if queue is not None:
            queue.enqueue(
                JobPayload(
                    workspace_id=runtime.workspace_id,
                    job_type=JobType.RUNTIME_CONTROL,
                    resource_id=runtime.id,
                    idempotency_key=f"admin.runtime.stop:{runtime.workspace_id}:{runtime.id}",
                    routing={
                        "action": "stop",
                        "reason": reason,
                        "source": "platform_admin",
                        "force": True,
                    },
                    priority=100,
                    max_attempts=3,
                )
            )
        self._session.add(
            RuntimeEvent(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type="runtime.force_stop_requested",
                message=reason,
                event_metadata={
                    "source": "platform_admin",
                    "runtime_lease_id": str(lease.id) if lease is not None else None,
                    "runtime_lease_release_pending": lease is not None,
                    "worker_control_enqueued": queue is not None,
                },
                created_at=now,
            )
        )
        self._session.commit()
        self._session.refresh(runtime)
        return runtime

    def list_platform_policies(
        self,
        page: PageParams,
        *,
        status: str | None = None,
    ) -> tuple[list[PlatformPolicy], int]:
        statement = select(PlatformPolicy)
        if status is not None:
            statement = statement.where(PlatformPolicy.status == status)
        return self._page(statement.order_by(PlatformPolicy.updated_at.desc()), page)

    def list_platform_policy_events(
        self,
        policy_key: str,
        page: PageParams,
        *,
        event_type: str | None = None,
    ) -> tuple[list[PlatformPolicyEvent], int] | None:
        policy = self._session.scalar(
            select(PlatformPolicy).where(PlatformPolicy.policy_key == policy_key)
        )
        if policy is None:
            return None
        statement = select(PlatformPolicyEvent).where(
            PlatformPolicyEvent.platform_policy_id == policy.id
        )
        if event_type is not None:
            statement = statement.where(PlatformPolicyEvent.event_type == event_type)
        return self._page(statement.order_by(PlatformPolicyEvent.created_at.desc()), page)

    def get_or_create_risky_execution_policy(self) -> PlatformPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == RISKY_EXECUTION_POLICY_KEY,
            )
        )
        if policy is not None:
            return policy
        policy = PlatformPolicy(
            policy_key=RISKY_EXECUTION_POLICY_KEY,
            status="active",
            value=default_risky_execution_policy_value(),
            description="Global personal-safety controls for risky execution capabilities.",
        )
        self._session.add(policy)
        self._session.flush([policy])
        self._append_policy_event(policy, "platform_policy.created", "Policy created", {})
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def get_or_create_worker_control_policy(self) -> PlatformPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == WORKER_CONTROL_POLICY_KEY,
            )
        )
        if policy is not None:
            return policy
        policy = PlatformPolicy(
            policy_key=WORKER_CONTROL_POLICY_KEY,
            status="active",
            value=default_worker_control_policy_value(),
            description="Global controls and audit stream for worker nodes.",
        )
        self._session.add(policy)
        self._session.flush([policy])
        self._append_policy_event(policy, "platform_policy.created", "Policy created", {})
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def update_risky_execution_policy(
        self,
        *,
        value: dict[str, object],
        updated_by: str | None,
        description: str | None = None,
    ) -> PlatformPolicy:
        policy = self.get_or_create_risky_execution_policy()
        policy.value = self._normalize_risky_execution_policy(value)
        if description is not None:
            policy.description = description
        policy.updated_by = updated_by
        self._append_policy_event(
            policy,
            "platform_policy.updated",
            "Risky execution policy updated",
            {"value": policy.value, "updated_by": updated_by},
        )
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def update_worker_control_policy(
        self,
        *,
        value: dict[str, object],
        updated_by: str | None,
        description: str | None = None,
    ) -> PlatformPolicy:
        policy = self.get_or_create_worker_control_policy()
        policy.value = normalize_worker_control_policy_value(policy.value, value)
        if description is not None:
            policy.description = description
        policy.updated_by = updated_by
        self._append_policy_event(
            policy,
            "platform_policy.updated",
            "Worker control policy updated",
            {"value": policy.value, "updated_by": updated_by},
        )
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def list_security_events(
        self,
        page: PageParams,
        *,
        severity: str | None = None,
        workspace_id: UUID | None = None,
    ) -> tuple[list[SecurityEvent], int]:
        statement = select(SecurityEvent)
        if severity is not None:
            statement = statement.where(SecurityEvent.severity == severity)
        if workspace_id is not None:
            statement = statement.where(SecurityEvent.workspace_id == workspace_id)
        return self._page(statement.order_by(SecurityEvent.created_at.desc()), page)

    def _normalize_risky_execution_policy(
        self,
        value: dict[str, object],
    ) -> dict[str, object]:
        return normalize_risky_execution_policy_value(
            self.get_or_create_risky_execution_policy().value,
            value,
        )

    def _worker_control_policy(self) -> WorkerControlPolicy:
        raw_policy = self.get_or_create_worker_control_policy()
        normalized = normalize_worker_control_policy_value(raw_policy.value, {})
        raw_policy.value = normalized
        return WorkerControlPolicy(
            managed_by=str(normalized["managed_by"]),
            allow_status_updates=normalized["allow_status_updates"] is True,
            allow_capacity_updates=normalized["allow_capacity_updates"] is True,
            allow_queue_updates=normalized["allow_queue_updates"] is True,
            allowed_statuses=tuple(
                item for item in normalized["allowed_statuses"] if isinstance(item, str)
            ),
            allowed_worker_types=tuple(
                item for item in normalized["allowed_worker_types"] if isinstance(item, str)
            ),
            max_capacity={
                str(key): value
                for key, value in dict(normalized["max_capacity"]).items()
                if isinstance(value, int)
            },
        )

    def _assert_worker_update_allowed(
        self,
        policy: WorkerControlPolicy,
        *,
        status: str | None,
        worker_type: str | None,
        queue_name: str | None,
        capacity: dict[str, object] | None,
    ) -> None:
        if status is not None:
            if not policy.allow_status_updates:
                raise PolicyDeniedError(
                    "Worker status updates are disabled by platform policy",
                    code="worker_status_update_denied",
                )
            if status not in policy.allowed_statuses:
                raise PolicyDeniedError(
                    "Worker status is not allowed by platform policy",
                    code="worker_status_not_allowed",
                    details={"status": status, "allowed_statuses": list(policy.allowed_statuses)},
                )
        if worker_type is not None and worker_type not in policy.allowed_worker_types:
            raise PolicyDeniedError(
                "Worker type is not allowed by platform policy",
                code="worker_type_not_allowed",
                details={
                    "worker_type": worker_type,
                    "allowed_worker_types": list(policy.allowed_worker_types),
                },
            )
        if queue_name is not None and not policy.allow_queue_updates:
            raise PolicyDeniedError(
                "Worker queue updates are disabled by platform policy",
                code="worker_queue_update_denied",
            )
        if capacity is not None:
            if not policy.allow_capacity_updates:
                raise PolicyDeniedError(
                    "Worker capacity updates are disabled by platform policy",
                    code="worker_capacity_update_denied",
                )
            exceeded = _first_exceeded_capacity_cap(capacity, policy.capacity_caps())
            if exceeded is not None:
                key, requested, cap = exceeded
                raise PolicyDeniedError(
                    "Worker capacity exceeds platform policy",
                    code="worker_capacity_exceeds_policy",
                    details={"capacity_key": key, "requested": requested, "max_allowed": cap},
                )

    def _append_policy_event(
        self,
        policy: PlatformPolicy,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            PlatformPolicyEvent(
                platform_policy_id=policy.id,
                event_type=event_type,
                message=message,
                event_metadata=metadata,
                created_at=datetime.now(UTC),
            )
        )

    def _append_worker_control_event(
        self,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        policy = self.get_or_create_worker_control_policy()
        self._append_policy_event(policy, event_type, message, metadata)

    def _count(self, statement: Select[tuple[T]]) -> int:
        count_statement = select(func.count()).select_from(statement.subquery())
        return int(self._session.scalar(count_statement) or 0)

    def _count_keys(self, pattern: str) -> int:
        if self._redis is None:
            return 0
        return sum(1 for _ in self._redis.scan_iter(pattern))

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
            max_jobs = _positive_int(worker.capacity.get("max_jobs"), 1)
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
        return _top_counts(counts, limit)

    def _top_security_reasons(self, limit: int = 5) -> list[dict[str, object]]:
        rows = self._session.scalars(select(SecurityEvent.reason)).all()
        counts: dict[str, int] = {}
        for reason in rows:
            key = reason if reason else "unknown"
            counts[key] = counts.get(key, 0) + 1
        return _top_counts(counts, limit)

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        return page_scalars(self._session, statement, page)


def _normalized_worker_capacity(
    capacity: dict[str, object],
    worker_type: str,
) -> dict[str, object]:
    normalized = dict(capacity)
    normalized.setdefault("worker_type", worker_type)
    return normalized


def _worker_node_snapshot(node: WorkerNode) -> dict[str, object]:
    return {
        "worker_id": node.worker_id,
        "worker_type": node.worker_type,
        "status": node.status,
        "queue_name": node.queue_name,
        "worker_version": node.worker_version,
        "hostname": node.hostname,
        "capacity": dict(node.capacity),
        "details": dict(node.details),
        "drain_requested_at": node.drain_requested_at.isoformat()
        if node.drain_requested_at is not None
        else None,
    }


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default


def _first_exceeded_capacity_cap(
    capacity: dict[str, object],
    caps: dict[str, int],
) -> tuple[str, int, int] | None:
    for key, cap in caps.items():
        value = capacity.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if value > cap:
            return key, value, cap
    return None


def _top_counts(counts: dict[str, int], limit: int) -> list[dict[str, object]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
