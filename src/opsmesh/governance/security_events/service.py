from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.shared.http.security_context import SecurityRequestContext
from opsmesh.shared.security.redaction import redact_sensitive_payload


class SecurityAuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_request_event(
        self,
        *,
        request_context: SecurityRequestContext,
        action: str,
        outcome: str,
        severity: str,
        reason: str,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SecurityEvent:
        event = SecurityEvent(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            outcome=outcome,
            severity=severity,
            source_ip=request_context.source_ip,
            user_agent=request_context.user_agent,
            request_id=request_context.request_id,
            path=request_context.path,
            method=request_context.method,
            reason=reason,
            event_metadata=redact_sensitive_payload(metadata or {}),
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event
