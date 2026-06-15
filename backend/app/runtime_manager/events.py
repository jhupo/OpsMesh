from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.runtime_spaces.models import RuntimeSpaceEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime


class RuntimeEventLog:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        created_at = datetime.now(UTC)
        event_metadata = {"runtime_id": str(runtime.id)} | (metadata or {})
        self._session.add(
            RuntimeEvent(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                event_metadata=event_metadata,
                created_at=created_at,
            )
        )
        if runtime.runtime_space_id is not None:
            self._session.add(
                RuntimeSpaceEvent(
                    workspace_id=runtime.workspace_id,
                    runtime_space_id=runtime.runtime_space_id,
                    event_type=event_type,
                    message=message,
                    event_metadata={
                        "runtime_id": str(runtime.id),
                        "runtime_status": runtime.status,
                        "connection_status": runtime.connection_status,
                        **(metadata or {}),
                    },
                    created_at=created_at,
                )
            )
