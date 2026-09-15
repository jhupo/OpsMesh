from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.observability.audit.security_models import SecurityEvent


@dataclass(frozen=True, slots=True)
class SecurityRequestContext:
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str


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
