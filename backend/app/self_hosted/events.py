from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runs.event_writer import RunEventWriter
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpaceEvent
from backend.app.runtimes.models import RuntimeEvent, WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.security.redaction import redact_sensitive_payload


class SelfHostedEventRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append_run_event(
        self,
        run: AgentRun,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> RunEvent:
        return RunEventWriter(self._session).append(
            workspace_id=run.workspace_id, run_id=run.id,
            event_type=event_type, message=message, metadata=metadata,
        )

    def append_runtime_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            workspace_id=runtime.workspace_id,
            workspace_runtime_id=runtime.id,
            event_type=event_type,
            message=message,
            event_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event

    def append_runtime_space_event(
        self,
        runtime: WorkspaceRuntime,
        event_type: str,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if runtime.runtime_space_id is None:
            return
        self._session.add(
            RuntimeSpaceEvent(
                workspace_id=runtime.workspace_id,
                runtime_space_id=runtime.runtime_space_id,
                event_type=event_type,
                message=message,
                event_metadata={"runtime_id": str(runtime.id), **(metadata or {})},
                created_at=datetime.now(UTC),
            )
        )

    def append_security_event(
        self,
        *,
        workspace_id: UUID,
        action: str,
        outcome: str,
        severity: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> SecurityEvent:
        event = SecurityEvent(
            workspace_id=workspace_id,
            user_id=None,
            action=action,
            outcome=outcome,
            severity=severity,
            source_ip=None,
            user_agent=None,
            request_id=None,
            path="self_hosted",
            method="SYSTEM",
            reason=reason[:512],
            event_metadata=redact_sensitive_payload(metadata or {}),
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event
