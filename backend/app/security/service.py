from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session
from starlette.requests import Request

from backend.app.core.client_ip import resolve_client_ip
from backend.app.security.models import SecurityEvent
from backend.app.security.redaction import redact_sensitive_payload


class SecurityAuditService:
    def __init__(self, session: Session, *, trusted_proxy_hops: int | None = None) -> None:
        self._session = session
        self._trusted_proxy_hops = trusted_proxy_hops

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
            source_ip=resolve_client_ip(
                request,
                trusted_proxy_hops=self._resolve_trusted_proxy_hops(request),
            ),
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

    def _resolve_trusted_proxy_hops(self, request: Request) -> int:
        if self._trusted_proxy_hops is not None:
            return self._trusted_proxy_hops
        app = request.scope.get("app")
        settings = getattr(getattr(app, "state", None), "settings", None)
        value = getattr(settings, "trusted_proxy_hops", 0)
        return value if isinstance(value, int) and value >= 0 else 0
