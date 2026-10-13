from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.runtime.instances.models import WorkspaceRuntime


class RuntimeSecurityEventRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_command_blocked(
        self,
        runtime: WorkspaceRuntime,
        *,
        reason: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            SecurityEvent(
                workspace_id=runtime.workspace_id,
                user_id=None,
                action="runtime.command.blocked",
                outcome="blocked",
                severity="high",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="runtime",
                method="SYSTEM",
                reason=reason[:512],
                event_metadata=runtime_security_metadata(runtime) | metadata,
                created_at=datetime.now(UTC),
            )
        )


def runtime_security_metadata(runtime: WorkspaceRuntime) -> dict[str, object]:
    return {
        "runtime_id": str(runtime.id),
        "runtime_space_id": str(runtime.runtime_space_id) if runtime.runtime_space_id else None,
        "sandbox_backend": runtime.sandbox_backend_id,
    }
