"""Host maintenance preserves page transactions and live logins."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.bootstrap import maintenance
from opsmesh.config import Settings
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.users.auth.models import LoginSession
from opsmesh.users.auth.service import token_digest
from opsmesh.users.service import create_user


def assemble(monkeypatch, sessions, cipher=None):
    engine = Mock()
    monkeypatch.setattr(
        maintenance,
        "build_database_resources",
        lambda _: SimpleNamespace(sessions=sessions, engine=engine, cipher=cipher),
    )
    return engine


def test_cleanup_commits_bounded_pages_and_retains_live_logins(database_sessions, monkeypatch):
    now = datetime.now(UTC)
    with database_sessions.begin() as session:
        user = create_user(session, email="cleanup@example.test", display_name="Cleanup")
        for index in range(202):
            session.add(
                LoginSession(
                    user_id=user.id,
                    token_hash=token_digest(uuid4()),
                    expires_at=now + timedelta(days=1 if index == 201 else -1),
                )
            )
    engine = assemble(monkeypatch, database_sessions)
    assert maintenance.maintain_database(Settings(), "cleanup-login-sessions", before=now) == {
        "command": "cleanup-login-sessions",
        "processed": 201,
    }
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "cleanup-login-sessions")
            )
            == 3
        )
    engine.dispose.assert_called_once()


def test_failed_audit_rolls_back_the_page(database_sessions, monkeypatch):
    with database_sessions.begin() as session:
        user = create_user(session, email="rollback@example.test", display_name="Rollback")
        session.add(
            LoginSession(
                user_id=user.id,
                token_hash=token_digest(uuid4()),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
    engine = assemble(monkeypatch, database_sessions)

    def reject(*args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(maintenance, "record_audit", reject)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        maintenance.maintain_database(Settings(), "cleanup-login-sessions")
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 1
    engine.dispose.assert_called_once()


def test_naive_cutoff_is_rejected_before_opening_database(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(maintenance, "build_database_resources", factory)
    with pytest.raises(ValueError, match="timezone"):
        maintenance.maintain_database(
            Settings(),
            "cleanup-login-sessions",
            before=datetime(2026, 1, 1),
        )
    factory.assert_not_called()
