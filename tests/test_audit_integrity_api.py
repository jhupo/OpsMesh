from collections.abc import Generator
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.governance.audit.activities import AuditIntegrityActivities
from opsmesh.governance.audit.models import AuditEvent, AuditIntegrityCheck
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.users.models import User
from opsmesh.main import create_app
from opsmesh.messaging.notifications.models import WorkspaceNotification
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine
from tests.fixtures.temporal import durable_executions

TOKEN = "audit-integrity-api-token"


def test_audit_integrity_api_queues_worker_verification_and_reports_status() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "audit-integrity")
    other_owner, _ = _seed_workspace(session, "audit-integrity-other")
    AuditService(session).record_user_action(
        workspace_id=workspace.id,
        user_id=owner.id,
        action="workspace.created",
        target_type="workspace",
        target_id=workspace.id,
    )
    session.commit()

    missing = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(owner.id),
    )
    queued = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity/verify",
        headers=_headers(owner.id),
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(other_owner.id),
    )

    assert missing.status_code == 200
    assert missing.json() == {"status": "missing", "stale": True, "latest": None}
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert forbidden.status_code == 403

    job = durable_executions(session, workspace.id, ExecutionKind.AUDIT_INTEGRITY)[-1]
    assert job is not None
    assert job.kind == ExecutionKind.AUDIT_INTEGRITY
    assert job.workspace_id == workspace.id
    AuditIntegrityActivities(
        ActivityDependencies(
            sessionmaker(session.get_bind(), expire_on_commit=False), client.app.state.settings
        )
    ).check_integrity(job)
    session.commit()

    status = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(owner.id),
    )
    assert status.status_code == 200
    assert status.json()["status"] == "valid"
    assert status.json()["stale"] is False
    assert status.json()["latest"]["checked_events"] == 2
    assert (
        session.scalar(
            select(AuditIntegrityCheck).where(AuditIntegrityCheck.workspace_id == workspace.id)
        )
        is not None
    )

    first_event = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace.id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .limit(1)
    )
    assert first_event is not None
    session.execute(
        update(AuditEvent)
        .where(AuditEvent.id == first_event.id)
        .values(current_hash="sha256:storage-corruption")
    )
    session.commit()

    requeued = client.post(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity/verify",
        headers=_headers(owner.id),
    )
    assert requeued.status_code == 202
    corrupt_job = durable_executions(session, workspace.id, ExecutionKind.AUDIT_INTEGRITY)[-1]
    assert corrupt_job is not None
    AuditIntegrityActivities(
        ActivityDependencies(
            sessionmaker(session.get_bind(), expire_on_commit=False), client.app.state.settings
        )
    ).check_integrity(corrupt_job)
    session.commit()

    invalid = client.get(
        f"/api/v1/workspaces/{workspace.id}/operations/audit-integrity",
        headers=_headers(owner.id),
    )
    notification = session.scalar(
        select(WorkspaceNotification).where(
            WorkspaceNotification.workspace_id == workspace.id,
            WorkspaceNotification.notification_type == "audit.integrity_invalid",
        )
    )
    assert invalid.status_code == 200
    assert invalid.json()["status"] == "invalid"
    assert notification is not None
    assert notification.severity == "critical"
    assert notification.metadata_["recommended_actions"]
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "audit.integrity_verification_queued",
            )
        )
        is not None
    )


def _client() -> tuple[TestClient, Session]:
    engine = product_engine()
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            database_url=engine.url.render_as_string(hide_password=False),
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    return TestClient(app), session


def _seed_workspace(session: Session, slug: str) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name=slug)
    workspace = Workspace(owner=user, name=slug, slug=f"{slug}-{uuid4()}", settings={})
    session.add_all(
        [
            user,
            workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
        ]
    )
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}
