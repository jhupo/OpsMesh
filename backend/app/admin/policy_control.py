from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from backend.app.admin.base import AdminSessionService
from backend.app.admin.common import first_exceeded_capacity_cap
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
from backend.app.core.errors import PolicyDeniedError


class AdminPolicyService(AdminSessionService):
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
        self.append_policy_event(policy, "platform_policy.created", "Policy created", {})
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
        self.append_policy_event(policy, "platform_policy.created", "Policy created", {})
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
        policy.value = self.normalize_risky_execution_policy(value)
        if description is not None:
            policy.description = description
        policy.updated_by = updated_by
        self.append_policy_event(
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
        self.append_policy_event(
            policy,
            "platform_policy.updated",
            "Worker control policy updated",
            {"value": policy.value, "updated_by": updated_by},
        )
        self._session.commit()
        self._session.refresh(policy)
        return policy

    def normalize_risky_execution_policy(self, value: dict[str, object]) -> dict[str, object]:
        return normalize_risky_execution_policy_value(
            self.get_or_create_risky_execution_policy().value,
            value,
        )

    def worker_control_policy(self) -> WorkerControlPolicy:
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

    def append_policy_event(
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

    def append_worker_control_event(
        self,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        policy = self.get_or_create_worker_control_policy()
        self.append_policy_event(policy, event_type, message, metadata)
