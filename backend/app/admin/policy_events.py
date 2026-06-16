from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent


class AdminPolicyEventService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
