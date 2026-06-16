from __future__ import annotations

from sqlalchemy import select

from backend.app.admin.base import AdminSessionService
from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.admin.policy_events import AdminPolicyEventService
from backend.app.admin.risky_policy_values import (
    RISKY_EXECUTION_POLICY_KEY,
    default_risky_execution_policy_value,
    normalize_risky_execution_policy_value,
)
from backend.app.admin.worker_policy_values import (
    WORKER_CONTROL_POLICY_KEY,
    default_worker_control_policy_value,
    normalize_worker_control_policy_value,
)
from backend.app.api.pagination import PageParams


class AdminPolicyService(AdminSessionService):
    def __init__(self, session) -> None:
        super().__init__(session)
        self._events = AdminPolicyEventService(session)

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
        self._events.append_policy_event(policy, "platform_policy.created", "Policy created", {})
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
        self._events.append_policy_event(policy, "platform_policy.created", "Policy created", {})
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
        self._events.append_policy_event(
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
        self._events.append_policy_event(
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
