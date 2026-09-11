from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent


class RuntimeSecurityEventRecorder:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_cleanup_failure(
        self,
        runtime: WorkspaceRuntime,
        cleanup: dict[str, object],
        *,
        reason: str,
    ) -> None:
        self._session.add(
            SecurityEvent(
                workspace_id=runtime.workspace_id,
                user_id=None,
                action="runtime.cleanup.failed",
                outcome="failed",
                severity="critical",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="runtime_manager",
                method="SYSTEM",
                reason=reason[:512],
                event_metadata=runtime_security_metadata(runtime) | cleanup,
                created_at=datetime.now(UTC),
            )
        )

    def record_policy_limit(
        self,
        runtime: WorkspaceRuntime,
        *,
        action: str,
        reason: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            SecurityEvent(
                workspace_id=runtime.workspace_id,
                user_id=None,
                action=action,
                outcome="limited",
                severity="warning",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="runtime_manager",
                method="SYSTEM",
                reason=reason[:512],
                event_metadata=runtime_security_metadata(runtime) | metadata,
                created_at=datetime.now(UTC),
            )
        )

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
                path="runtime_manager",
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
        "docker_container_id": runtime.docker_container_id,
    }
