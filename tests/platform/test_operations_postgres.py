"""Atomic command effects, Outbox, current authorization and cancellation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from opsmesh.bootstrap.platform import operation_handlers
from opsmesh.identity.auth.models import LoginSession
from opsmesh.identity.auth.service import token_digest
from opsmesh.identity.users.models import UserProfile
from opsmesh.identity.users.service import create_user, identity_of
from opsmesh.platform.operations import service
from opsmesh.platform.operations.activities import PlatformOperationActivities
from opsmesh.platform.operations.admission import is_open
from opsmesh.platform.operations.models import PlatformAdmission, PlatformOperation
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

CIPHER = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")


@pytest.fixture
def admin(database_sessions):
    with database_sessions() as session, session.begin():
        user = create_user(
            session,
            email="platform@example.com",
            display_name="Platform",
            platform_roles=frozenset({"admin"}),
        )
        return identity_of(user)


def submit(database_sessions, admin, *, command="admission.drain", owner="platform", target=None):
    with database_sessions() as session, session.begin():
        return service.submit(
            session,
            owner=owner,
            command=command,
            target=target or {},
            idempotency_key=command,
            actor=admin,
            handlers=operation_handlers(CIPHER),
        ).id


def test_admission_is_durable_and_idempotent(database_sessions, admin):
    identifier = submit(database_sessions, admin)
    assert submit(database_sessions, admin) == identifier
    with database_sessions() as session:
        assert is_open(session)
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(TemporalOutboxEntry.workflow_id == f"platform-operation:{identifier}")
            )
            == 1
        )
    activities = PlatformOperationActivities(database_sessions, operation_handlers(CIPHER))
    assert activities.execute_transaction(identifier) == "completed"
    assert activities.execute_transaction(identifier) == "completed"
    with database_sessions() as session:
        assert not is_open(session)
        assert session.scalar(select(PlatformAdmission.revision)) == 1
    resume = submit(database_sessions, admin, command="admission.resume")
    assert activities.execute_transaction(resume) == "completed"
    with database_sessions() as session:
        assert is_open(session)


def test_current_admin_rights_are_required_when_claiming(database_sessions, admin):
    identifier = submit(database_sessions, admin)
    with database_sessions() as session, session.begin():
        session.get(UserProfile, admin.user_id).platform_roles = ["member"]
    activities = PlatformOperationActivities(database_sessions, operation_handlers(CIPHER))
    assert activities.execute_transaction(identifier) == "needs_attention"
    with database_sessions() as session:
        assert is_open(session)


def test_cancelled_operation_never_executes(database_sessions, admin):
    identifier = submit(database_sessions, admin)
    with database_sessions() as session, session.begin():
        service.cancel(session, operation_id=identifier, actor=admin)
    activities = PlatformOperationActivities(database_sessions, operation_handlers(CIPHER))
    assert activities.execute_transaction(identifier) == "cancelled"
    with database_sessions() as session:
        assert is_open(session)


def test_resumed_operation_rejects_previous_generation_activity(database_sessions, admin):
    identifier = submit(database_sessions, admin)
    with database_sessions() as session, session.begin():
        service.cancel(session, operation_id=identifier, actor=admin)
        resumed = service.resume(
            session, operation_id=identifier, actor=admin, handlers=operation_handlers(CIPHER)
        )
        assert resumed.generation == 2
    activities = PlatformOperationActivities(database_sessions, operation_handlers(CIPHER))
    assert activities.execute_transaction(identifier, generation=1) == "cancelled"
    with database_sessions() as session:
        assert is_open(session)
        assert session.get(PlatformOperation, identifier).status == "accepted"
    assert activities.execute_transaction(identifier, generation=2) == "completed"


def test_rollback_discards_operation_and_outbox(database_sessions, admin):
    with database_sessions() as session:
        service.submit(
            session,
            owner="platform",
            command="admission.drain",
            target={},
            idempotency_key="rolled-back",
            actor=admin,
            handlers=operation_handlers(CIPHER),
        )
        session.rollback()
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(PlatformOperation)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(TemporalOutboxEntry.workflow_id.like("platform-operation:%"))
            )
            == 0
        )


def test_unknown_command_is_rejected_before_persistence(database_sessions, admin):
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        service.submit(
            session,
            owner="platform",
            command="host.shell",
            target={},
            idempotency_key="unknown",
            actor=admin,
            handlers=operation_handlers(CIPHER),
        )


def test_cleanup_checkpoints_batches_and_keeps_live_sessions(database_sessions, admin):
    from uuid import uuid4

    now = datetime.now(UTC)
    with database_sessions() as session, session.begin():
        for i in range(202):
            session.add(
                LoginSession(
                    user_id=admin.user_id,
                    token_hash=token_digest(uuid4()),
                    expires_at=now + timedelta(days=1 if i == 201 else -1),
                )
            )
    identifier = submit(
        database_sessions,
        admin,
        command="cleanup",
        owner="identity.sessions",
        target={"before": now.isoformat()},
    )
    activities = PlatformOperationActivities(database_sessions, operation_handlers(CIPHER))
    assert activities.execute_transaction(identifier) == "running"
    assert activities.execute_transaction(identifier) == "running"
    assert activities.execute_transaction(identifier) == "completed"
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 1
        assert session.get(PlatformOperation, identifier).result["processed"] == 201
