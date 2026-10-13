from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from opsmesh.governance.policies.models import PlatformPolicy, PlatformPolicyEvent


def append_policy_event(
    session: Session,
    policy: PlatformPolicy,
    event_type: str,
    message: str,
    metadata: dict[str, object],
) -> None:
    session.add(
        PlatformPolicyEvent(
            platform_policy_id=policy.id,
            event_type=event_type,
            message=message,
            event_metadata=metadata,
            created_at=datetime.now(UTC),
        )
    )
