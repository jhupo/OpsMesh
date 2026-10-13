from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.queries import AuditQueryService
from opsmesh.governance.audit.service import AuditService
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.governance.security_events.service import SecurityEventRecorder
from opsmesh.identity.users.models import User
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.http.client_ip import security_request_context
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.redaction import redact_sensitive_payload, redact_sensitive_text
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine


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

    SecurityEventRecorder(session).record_request_event(
        request_context=security_request_context(_request()),
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
            {"note": "OpenAI key is sk-message-payload-secret"},
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


def test_integrity_check_persists_tamper_evidence_for_each_workspace() -> None:
    session = _session()
    user, first_workspace = _seed_workspace(session)
    second_workspace = Workspace(
        owner=user,
        name="Second",
        slug=str(uuid4()),
        settings={},
    )
    session.add_all(
        [
            second_workspace,
            WorkspaceMember(workspace=second_workspace, user=user, role="owner"),
        ]
    )
    session.commit()
    AuditService(session).record_user_action(
        workspace_id=first_workspace.id,
        user_id=user.id,
        action="workspace.created",
        target_type="workspace",
        target_id=first_workspace.id,
    )
    session.commit()
    now = datetime.now(UTC)
    integrity = AuditIntegrityService(session)

    assert (
        integrity.verify_and_record_workspace_integrity(first_workspace.id, checked_at=now).valid
        is True
    )
    assert (
        integrity.verify_and_record_workspace_integrity(second_workspace.id, checked_at=now).valid
        is True
    )
    session.commit()

    event_id = session.scalar(
        select(AuditEvent.id).where(AuditEvent.workspace_id == first_workspace.id)
    )
    assert event_id is not None
    session.execute(update(AuditEvent).where(AuditEvent.id == event_id).values(action="tampered"))
    session.commit()
    check = integrity.verify_and_record_workspace_integrity(
        first_workspace.id,
        checked_at=now + timedelta(hours=2),
    )
    session.commit()

    assert check.valid is False
    assert check.broken_event_id == event_id
    assert check.reason == "current_hash_mismatch"
    assert integrity.latest(first_workspace.id).id == check.id


def test_audit_retention_filters_queries_without_deleting_history() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    now = datetime.now(UTC)
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
    items, total = AuditQueryService(session, settings).list_events(
        workspace.id,
        PageParams(limit=10, offset=0),
    )

    assert total == 1
    assert [item.action for item in items] == ["workspace.fresh"]

    assert session.get(AuditEvent, old_event.id) is not None


def _session() -> Session:
    engine = product_engine()
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
        id=uuid4(),
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
