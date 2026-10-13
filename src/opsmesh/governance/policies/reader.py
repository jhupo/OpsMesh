from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.governance.policies.models import PlatformPolicy
from opsmesh.governance.policies.risky_values import (
    RISKY_EXECUTION_POLICY_KEY,
    RiskyExecutionPolicy,
    risky_execution_policy_from_value,
)


def risky_execution_policy(session: Session) -> RiskyExecutionPolicy:
    policy = session.scalar(
        select(PlatformPolicy).where(
            PlatformPolicy.policy_key == RISKY_EXECUTION_POLICY_KEY,
            PlatformPolicy.status == "active",
        )
    )
    if policy is None or not isinstance(policy.value, dict):
        return RiskyExecutionPolicy()
    return risky_execution_policy_from_value(policy.value)
