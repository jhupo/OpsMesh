from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from backend.app.api.pagination import PageParams
from backend.app.api.services.workspace_reads import WorkspaceReadService
from backend.app.audit.models import AuditEvent
from backend.app.audit.service import AuditService
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.security.models import SecurityEvent
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.security.service import SecurityAuditService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_audit_service_redacts_sensitive_metadata_before_db_write() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)

    AuditService(session).record_user_action(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.updated",
        target_type="workspace",
        target_id=workspace.id,
        metadata={
            "safe": "visible",
            "token": "audit-token",
            "nested": {
                "api_key": "sk-audit",
                "items": [
                    {"authorization": "Bearer audit-secret"},
                    {"safe": "still-visible", "password": "audit-password"},
                ],
            },
            "request_headers": {"x-api-key": "nested-header-secret"},
        },
    )
    session.commit()
    session.expire_all()

    raw_metadata = session.execute(select(AuditEvent.audit_metadata)).scalar_one()

    assert raw_metadata == {
        "safe": "visible",
        "token": "[redacted]",
        "nested": {
            "api_key": "[redacted]",
            "items": [
                {"authorization": "[redacted]"},
                {"safe": "still-visible", "password": "[redacted]"},
            ],
        },
        "request_headers": "[redacted]",
    }
    assert "audit-token" not in str(raw_metadata)
    assert "audit-secret" not in str(raw_metadata)
    assert "audit-password" not in str(raw_metadata)
    assert "nested-header-secret" not in str(raw_metadata)


def test_security_audit_service_redacts_sensitive_metadata_before_db_write() -> None:
    session = _session()

    SecurityAuditService(session).record_request_event(
        request=_request(),
        action="auth.failed",
        outcome="denied",
        severity="high",
        reason="invalid credentials",
        metadata={
            "safe": "visible",
            "headers": {"authorization": "Bearer security-token"},
            "provider": {"base_url": "https://router.example.test/private"},
            "attempts": [
                {"secret": "security-secret"},
                {"Authorization": "Bearer second-secret"},
            ],
        },
    )
    session.commit()
    session.expire_all()

    raw_metadata = session.execute(select(SecurityEvent.event_metadata)).scalar_one()

    assert raw_metadata == {
        "safe": "visible",
        "headers": "[redacted]",
        "provider": {"base_url": "[redacted]"},
        "attempts": [
            {"secret": "[redacted]"},
            {"Authorization": "[redacted]"},
        ],
    }
    assert "security-token" not in str(raw_metadata)
    assert "router.example.test/private" not in str(raw_metadata)
    assert "security-secret" not in str(raw_metadata)
    assert "second-secret" not in str(raw_metadata)


def test_redaction_scrubs_sensitive_string_values_recursively() -> None:
    payload = {
        "safe": "token budget remains visible",
        "prompt": "Use Bearer hidden-provider-token for this call",
        "items": [
            {"note": "Claude key is sk-message-payload-secret"},
            "password=plain-secret",
        ],
        "nested": {"content": "api_key: sk-nested-secret"},
    }

    redacted = redact_sensitive_payload(payload)

    assert redacted == {
        "safe": "token budget remains visible",
        "prompt": "[redacted]",
        "items": [
            {"note": "[redacted]"},
            "[redacted]",
        ],
        "nested": {"content": "[redacted]"},
    }
    assert redact_sensitive_text("normal planning text") == "normal planning text"
    assert "hidden-provider-token" not in str(redacted)
    assert "sk-message-payload-secret" not in str(redacted)
    assert "plain-secret" not in str(redacted)


def test_audit_service_writes_verifiable_hash_chain() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    service = AuditService(session)

    first = service.record_user_action(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
        metadata={"safe": "first"},
    )
    second = service.record_user_action(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.updated",
        target_type="workspace",
        target_id=workspace.id,
        metadata={"safe": "second"},
    )
    session.commit()
    session.expire_all()

    assert first.previous_hash is None
    assert first.current_hash == AuditService.calculate_event_hash(first)
    assert first.current_hash is not None
    assert first.current_hash.startswith("sha256:")
    assert second.previous_hash == first.current_hash
    assert second.current_hash == AuditService.calculate_event_hash(second)

    verification = service.verify_workspace_hash_chain(workspace.id)
    assert verification.valid is True
    assert verification.checked_events == 2


def test_audit_events_are_append_only_and_worm_protected() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    event = AuditService(session).record_user_action(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
    )
    session.commit()

    event.action = "workspace.tampered"
    with pytest.raises(ValueError, match="append-only"):
        session.commit()
    session.rollback()

    event = session.get(AuditEvent, event.id)
    assert event is not None
    session.delete(event)
    with pytest.raises(ValueError, match="WORM"):
        session.commit()
    session.rollback()

    assert session.get(AuditEvent, event.id) is not None


def test_audit_retention_filters_queries_but_worm_cleanup_retains_rows() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    now = datetime(2026, 6, 5, tzinfo=UTC)
    old_event = _audit_event(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.old",
        created_at=now - timedelta(days=45),
    )
    fresh_event = _audit_event(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.fresh",
        created_at=now - timedelta(days=5),
    )
    session.add_all([old_event, fresh_event])
    session.commit()

    settings = Settings(audit_event_retention_days=30)
    items, total = WorkspaceReadService(session, settings).list_audit_events(
        workspace.id,
        PageParams(limit=10, offset=0),
    )

    assert total == 1
    assert [item.action for item in items] == ["workspace.fresh"]

    cleanup = AuditService(session, settings).cleanup_expired_events(
        workspace_id=workspace.id,
        now=now,
    )

    assert cleanup.reason == "worm_retained"
    assert cleanup.eligible_count == 1
    assert cleanup.deleted_count == 0
    assert cleanup.retained_count == 1
    assert session.get(AuditEvent, old_event.id) is not None


def test_audit_retention_can_delete_expired_rows_when_worm_is_disabled() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    now = datetime(2026, 6, 5, tzinfo=UTC)
    old_event = _audit_event(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.old",
        created_at=now - timedelta(days=45),
    )
    fresh_event = _audit_event(
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.fresh",
        created_at=now - timedelta(days=5),
    )
    session.add_all([old_event, fresh_event])
    session.commit()

    cleanup = AuditService(
        session,
        Settings(audit_event_retention_days=30, audit_event_worm_enabled=False),
    ).cleanup_expired_events(workspace_id=workspace.id, now=now)
    session.commit()

    assert cleanup.reason == "deleted_expired_events"
    assert cleanup.eligible_count == 1
    assert cleanup.deleted_count == 1
    assert session.get(AuditEvent, old_event.id) is None
    assert session.get(AuditEvent, fresh_event.id) is not None


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _audit_event(
    *,
    workspace_id: UUID,
    user_id: UUID,
    action: str,
    created_at: datetime,
) -> AuditEvent:
    return AuditEvent(
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=str(user_id),
        user_id=user_id,
        action=action,
        target_type="workspace",
        target_id=str(workspace_id),
        audit_metadata={},
        created_at=created_at,
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth",
            "query_string": b"",
            "headers": [
                (b"user-agent", b"pytest"),
                (b"x-forwarded-for", b"203.0.113.10, 10.0.0.1"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
