from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.app.security.models import SecurityEvent
from backend.app.security.redaction import redact_sensitive_payload


class SecurityAuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_request_event(
        self,
        *,
        request: Request,
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
            source_ip=self._source_ip(request),
            user_agent=request.headers.get("user-agent"),
            request_id=getattr(request.state, "request_id", None),
            path=request.url.path,
            method=request.method,
            reason=reason,
            event_metadata=redact_sensitive_payload(metadata or {}),
            created_at=datetime.now(UTC),
        )
        self._session.add(event)
        return event

    @staticmethod
    def _source_ip(request: Request) -> str | None:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", maxsplit=1)[0].strip()
        if request.client is None:
            return None
        return request.client.host
