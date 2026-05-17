from collections.abc import Generator
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.self_hosted.models import RuntimeCredential
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_self_hosted_runtime_registration_and_job_flow() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)

    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "mac-studio"},
    )
    assert enrollment.status_code == 201
    assert enrollment.json()["token"].startswith("ccrt_")

    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "mac-studio",
            "machine_id": "machine-1",
            "version": "0.1.0",
            "capabilities": {"gpu": True},
        },
    )
    assert registered.status_code == 201
    runtime_id = UUID(registered.json()["workspace_runtime_id"])
    credential = registered.json()["credential_token"]

    heartbeat = client.post(
        "/api/v1/self-hosted/heartbeat",
        headers=_runtime_headers(credential),
        json={"status": "online", "capabilities": {"gpu": True, "ram_gb": 64}},
    )
    assert heartbeat.status_code == 200

    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        title="Private render",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime_id,
        input={"prompt": "render locally"},
    )
    session.add(run)
    session.commit()

    next_job = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))
    assert next_job.status_code == 200
    assert next_job.json()["agent_run_id"] == str(run.id)

    claim = client.post(
        f"/api/v1/self-hosted/jobs/{run.id}/claim",
        headers=_runtime_headers(credential),
    )
    assert claim.status_code == 200
    session.refresh(run)
    assert run.status == "running"

    progress = client.post(
        "/api/v1/self-hosted/progress",
        headers=_runtime_headers(credential),
        json={
            "agent_run_id": str(run.id),
            "event_type": "self_hosted.progress",
            "message": "50%",
            "metadata": {"percent": 50},
        },
    )
    assert progress.status_code == 200

    local_file = client.post(
        "/api/v1/self-hosted/local-files",
        headers=_runtime_headers(credential),
        json={"task_id": str(task.id), "path": "/Users/me/data.csv", "label": "private data"},
    )
    assert local_file.status_code == 201

    artifact = client.post(
        "/api/v1/self-hosted/artifact-uploads",
        headers=_runtime_headers(credential),
        json={
            "agent_run_id": str(run.id),
            "filename": "result.png",
            "storage_key": f"workspaces/{workspace.id}/self-hosted/result.png",
            "checksum_sha256": "a" * 64,
        },
    )
    assert artifact.status_code == 201
    assert session.query(RunEvent).count() == 2

    runtime_credential = session.query(RuntimeCredential).one()
    revoked = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/credentials/{runtime_credential.id}/revoke",
        headers=_headers(owner.id),
    )
    assert revoked.status_code == 204
    denied = client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))
    assert denied.status_code == 401


def test_self_hosted_artifact_upload_rejects_cross_workspace_storage_key() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-3",
        },
    )
    credential = registered.json()["credential_token"]

    rejected = client.post(
        "/api/v1/self-hosted/artifact-uploads",
        headers=_runtime_headers(credential),
        json={
            "filename": "../result.png",
            "storage_key": "workspaces/other/self-hosted/result.png",
        },
    )

    assert rejected.status_code == 404


def test_runtime_credentials_are_bound_to_token_hash_pepper() -> None:
    client, session = _client(
        Settings(environment="test", log_format="text", internal_api_token=TOKEN)
    )
    owner, workspace = _seed_workspace(session)
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": "node"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": "node",
            "machine_id": "machine-2",
        },
    )
    credential = registered.json()["credential_token"]

    wrong_settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        token_hash_pepper="different-pepper",
    )
    app = create_app(wrong_settings)

    def override_db_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: wrong_settings
    wrong_client = TestClient(app)

    denied = wrong_client.get("/api/v1/self-hosted/jobs/next", headers=_runtime_headers(credential))

    assert denied.status_code == 401


def _client(settings: Settings | None = None) -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    app_settings = settings or Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
    )
    app = create_app(app_settings)

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    return TestClient(app), session


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="owner")
    workspace = Workspace(owner=user, name="Owner", slug="owner", settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _runtime_headers(token: str) -> dict[str, str]:
    return {"X-Runtime-Authorization": f"Bearer {token}"}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
