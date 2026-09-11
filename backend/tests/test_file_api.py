from collections.abc import Generator
from pathlib import Path

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.observability.audit_models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.files.models import FileAccessEvent
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.memory.jobs import enqueue_workspace_memory_index_job
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_file_upload_download_and_cross_workspace_denial(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"hello", "text/plain")},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]
    assert uploaded.json()["checksum_sha256"]
    assert "storage_key" not in uploaded.json()
    assert uploaded.json()["has_storage_object"] is True

    downloaded = client.get(
        f"/api/v1/workspaces/{workspace.id}/files/{file_id}/download",
        headers=_headers(owner.id),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"hello"
    assert session.query(FileAccessEvent).count() == 1

    denied = client.get(
        f"/api/v1/workspaces/{workspace.id}/files/{file_id}/download",
        headers=_headers(other.id),
    )
    assert denied.status_code == 403

    not_found = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/files/{file_id}/download",
        headers=_headers(other.id),
    )
    assert not_found.status_code == 404


def test_file_upload_enqueues_workspace_file_memory_index_job(tmp_path: Path) -> None:
    queue = _queue()
    client, session = _client(tmp_path, queue=queue)
    owner, workspace = _seed_workspace(session)

    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"hello", "text/plain")},
    )
    job = queue.dequeue()

    assert uploaded.status_code == 201
    assert job is not None
    assert job.workspace_id == workspace.id
    assert str(job.resource_id) == uploaded.json()["id"]
    assert job.requested_by_user_id == owner.id
    assert job.job_type == JobType.MEMORY_INDEX
    assert job.idempotency_key == (
        f"memory.index:{workspace.id}:workspace_file:{uploaded.json()['id']}"
    )
    assert job.routing == {
        "source": "workspace_file_upload",
        "source_type": "workspace_file",
    }
    assert (
        enqueue_workspace_memory_index_job(
            queue=queue,
            workspace_id=workspace.id,
            source_type="workspace_file",
            source_id=job.resource_id,
            requested_by_user_id=owner.id,
        )
        is False
    )


def test_upload_size_limit(tmp_path: Path) -> None:
    client, session = _client(tmp_path, max_upload_bytes=3)
    owner, workspace = _seed_workspace(session)

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("too-big.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 413


def test_workspace_file_runtime_policy_is_explicit_and_restricted_is_fail_closed(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session)
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"hello", "text/plain")},
    )
    file_id = uploaded.json()["id"]

    updated = client.put(
        f"/api/v1/workspaces/{workspace.id}/files/{file_id}/runtime-policy",
        headers=_headers(owner.id),
        json={"sensitivity": "confidential", "runtime_access": "denied"},
    )
    unsafe_restricted = client.put(
        f"/api/v1/workspaces/{workspace.id}/files/{file_id}/runtime-policy",
        headers=_headers(owner.id),
        json={"sensitivity": "restricted", "runtime_access": "allowed"},
    )

    assert uploaded.json()["sensitivity"] == "internal"
    assert uploaded.json()["runtime_access"] == "allowed"
    assert updated.status_code == 200
    assert updated.json()["sensitivity"] == "confidential"
    assert updated.json()["runtime_access"] == "denied"
    assert unsafe_restricted.status_code == 422
    assert (
        session.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == workspace.id,
                AuditEvent.action == "file.runtime_policy.updated",
            )
        )
        is not None
    )


def test_upload_sanitizes_filename_and_download_header(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session)

    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("../evil\r\n.txt", b"hello", "text/plain")},
    )
    file_id = uploaded.json()["id"]
    downloaded = client.get(
        f"/api/v1/workspaces/{workspace.id}/files/{file_id}/download",
        headers=_headers(owner.id),
    )

    assert uploaded.status_code == 201
    assert uploaded.json()["filename"] == "evil__.txt"
    assert "filename=\"evil__.txt\"" in downloaded.headers["content-disposition"]
    assert "\r" not in downloaded.headers["content-disposition"]
    assert "\n" not in downloaded.headers["content-disposition"]


def _client(
    tmp_path: Path,
    max_upload_bytes: int = 1024,
    queue: RedisQueue | None = None,
) -> tuple[TestClient, Session]:
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
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
            max_upload_bytes=max_upload_bytes,
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
    app.dependency_overrides[get_worker_queue] = lambda: queue or _queue()
    return TestClient(app), session


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
