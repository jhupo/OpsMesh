from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent


class RuntimeSpaceEventLog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        runtime_space: RuntimeSpace,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeSpaceEvent:
        event = RuntimeSpaceEvent(
            workspace_id=runtime_space.workspace_id,
            runtime_space_id=runtime_space.id,
            event_type=event_type,
            message=message,
            event_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event
