from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.observability.telemetry.request_context import current_evidence_context
from backend.app.runtime.environment.models import RuntimeEvent, WorkspaceRuntime
from backend.app.runtime.environment.spaces.models import RuntimeSpaceEvent


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
            new_runtime_event(
                workspace_id=runtime.workspace_id,
                workspace_runtime_id=runtime.id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                metadata=event_metadata,
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


def new_runtime_event(
    *,
    workspace_id: UUID,
    workspace_runtime_id: UUID,
    runtime_space_id: UUID | None,
    event_type: str,
    message: str,
    metadata: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> RuntimeEvent:
    evidence = current_evidence_context()
    return RuntimeEvent(
        workspace_id=workspace_id,
        workspace_runtime_id=workspace_runtime_id,
        runtime_space_id=runtime_space_id,
        event_type=event_type,
        message=redact_sensitive_text(message),
        request_id=evidence.get("request_id"),
        trace_id=evidence.get("trace_id"),
        span_id=evidence.get("span_id"),
        worker_id=evidence.get("worker_id"),
        runtime_id=str(workspace_runtime_id),
        event_metadata=redact_sensitive_payload(dict(metadata or {}) | evidence),
        created_at=created_at or datetime.now(UTC),
    )
