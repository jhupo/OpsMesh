from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.common import first_exceeded_capacity_cap
from backend.app.admin.models import PlatformPolicy
from backend.app.admin.policy_events import AdminPolicyEventService
from backend.app.admin.worker_policy_values import (
    WORKER_CONTROL_POLICY_KEY,
    WorkerControlPolicy,
    default_worker_control_policy_value,
    normalize_worker_control_policy_value,
    worker_control_policy_from_value,
)
from backend.app.core.errors import PolicyDeniedError


class AdminWorkerPolicyControlService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = AdminPolicyEventService(session)

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
        self._events.append_policy_event(policy, "platform_policy.created", "Policy created", {})
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def worker_control_policy(self) -> WorkerControlPolicy:
        raw_policy = self.get_or_create_worker_control_policy()
        normalized = normalize_worker_control_policy_value(raw_policy.value, {})
        raw_policy.value = normalized
        return worker_control_policy_from_value(normalized)

    def append_worker_control_event(
        self,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        policy = self.get_or_create_worker_control_policy()
        self._events.append_policy_event(policy, event_type, message, metadata)

    def assert_worker_update_allowed(
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
            exceeded = first_exceeded_capacity_cap(capacity, policy.capacity_caps())
            if exceeded is not None:
                key, requested, cap = exceeded
                raise PolicyDeniedError(
                    "Worker capacity exceeds platform policy",
                    code="worker_capacity_exceeds_policy",
                    details={"capacity_key": key, "requested": requested, "max_allowed": cap},
                )
