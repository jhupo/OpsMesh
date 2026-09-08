from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.trace_context import with_current_trace_metadata
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text


class RunEventWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self, *, workspace_id: UUID, run_id: UUID, event_type: str, message: str,
        metadata: dict[str, object] | None = None,
    ) -> RunEvent:
        # Serialize sequence allocation on the parent run until the caller commits.
        with self._session.no_autoflush:
            run = self._session.scalar(select(AgentRun).where(
                AgentRun.id == run_id, AgentRun.workspace_id == workspace_id,
            ).with_for_update())
        if run is None:
            raise ValueError("Run event target not found in workspace")
        self._session.flush()
        sequence = (self._session.scalar(select(func.max(RunEvent.sequence)).where(
            RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id == run_id,
        )) or 0) + 1
        event = RunEvent(
            workspace_id=workspace_id, agent_run_id=run_id, sequence=sequence,
            event_type=event_type, message=redact_sensitive_text(message),
            event_metadata=redact_sensitive_payload(with_current_trace_metadata(metadata)),
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        self._session.flush([event])
        return event
