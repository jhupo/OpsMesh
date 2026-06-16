from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.admin.models import PlatformPolicy
from backend.app.admin.risky_policy_values import (
    RISKY_EXECUTION_POLICY_KEY,
    RiskyExecutionPolicy,
    risky_execution_policy_from_value,
)
from backend.app.admin.worker_policy_values import (
    WORKER_CONTROL_POLICY_KEY,
    WorkerControlPolicy,
    worker_control_policy_from_value,
)


class PlatformPolicyService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def risky_execution_policy(self) -> RiskyExecutionPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == RISKY_EXECUTION_POLICY_KEY,
                PlatformPolicy.status == "active",
            )
        )
        if policy is None or not isinstance(policy.value, dict):
            return RiskyExecutionPolicy()
        return risky_execution_policy_from_value(policy.value)

    def worker_control_policy(self) -> WorkerControlPolicy:
        policy = self._session.scalar(
            select(PlatformPolicy).where(
                PlatformPolicy.policy_key == WORKER_CONTROL_POLICY_KEY,
                PlatformPolicy.status == "active",
            )
        )
        value = policy.value if policy is not None and isinstance(policy.value, dict) else {}
        return worker_control_policy_from_value(value)
