from collections.abc import Generator
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.files.models import WorkspaceFile
from backend.app.identity.models import User
from backend.app.main import create_app_with_dependencies
from backend.app.projects.policy import validate_project_configuration
from backend.app.rate_limits.service import RedisFixedWindowRateLimiter
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "project-test-token"


def test_project_api_manages_scoped_layout_inputs_and_outputs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", slug="primary")
    _, foreign_workspace = _seed_workspace(session, role="owner", slug="foreign")
    source_file = _file(session, workspace.id, owner.id, "requirements.md")
    foreign_file = _file(session, foreign_workspace.id, None, "foreign.txt")
    headers = _headers(owner.id)

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects",
        headers=headers,
        json={
            "name": "Backend",
            "slug": "backend",
            "input_path": "src/inputs",
            "work_path": "src/work",
            "output_path": "dist",
            "configuration": {"python": {"version": "3.12"}},
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    attached = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects/{project_id}/input-files",
        headers=headers,
        json={
            "workspace_file_id": str(source_file.id),
            "project_path": "src/inputs/requirements.md",
            "access_mode": "read_only",
        },
    )
    output = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects/{project_id}/outputs",
        headers=headers,
        json={
            "project_path": "dist/report.json",
            "artifact_type": "report",
            "content_type": "application/json",
            "required": True,
            "max_bytes": 4096,
        },
    )
    detail = client.get(f"/api/v1/workspaces/{workspace.id}/projects/{project_id}", headers=headers)
    foreign_attach = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects/{project_id}/input-files",
        headers=headers,
        json={
            "workspace_file_id": str(foreign_file.id),
            "project_path": "src/inputs/foreign.txt",
        },
    )
    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=headers,
        json={"title": "Build", "workspace_project_id": project_id},
    )
    removed = client.delete(
        f"/api/v1/workspaces/{workspace.id}/projects/{project_id}/input-files/"
        f"{attached.json()['id']}",
        headers=headers,
    )
    reattached = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects/{project_id}/input-files",
        headers=headers,
        json={
            "workspace_file_id": str(source_file.id),
            "project_path": "src/inputs/requirements.md",
            "access_mode": "copy_on_write",
        },
    )

    assert attached.status_code == 201
    assert output.status_code == 201
    assert detail.status_code == 200
    assert detail.json()["project"]["configuration"] == {"python": {"version": "3.12"}}
    assert detail.json()["input_files"][0]["workspace_file_id"] == str(source_file.id)
    assert detail.json()["outputs"][0]["project_path"] == "dist/report.json"
    assert foreign_attach.status_code == 404
    assert task.status_code == 201
    assert task.json()["workspace_project_id"] == project_id
    assert removed.status_code == 204
    assert reattached.status_code == 201
    actions = set(session.scalars(select(AuditEvent.action)).all())
    assert {
        "workspace_project.created",
        "workspace_project.input_file_added",
        "workspace_project.output_declared",
    } <= actions


def test_project_api_rejects_unsafe_layout_and_viewer_writes() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, role="owner", slug="owner")
    viewer, _ = _add_member(session, workspace, role="viewer")

    traversal = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects",
        headers=_headers(owner.id),
        json={"name": "Unsafe", "slug": "unsafe", "input_path": "../inputs"},
    )
    overlap = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects",
        headers=_headers(owner.id),
        json={
            "name": "Overlap",
            "slug": "overlap",
            "input_path": "project",
            "work_path": "project/work",
        },
    )
    viewer_write = client.post(
        f"/api/v1/workspaces/{workspace.id}/projects",
        headers=_headers(viewer.id),
        json={"name": "Denied", "slug": "denied"},
    )

    assert traversal.status_code == 400
    assert overlap.status_code == 400
    assert viewer_write.status_code == 403
    try:
        validate_project_configuration({"provider": {"api_key": "hidden"}})
    except ValueError as exc:
        assert "credentials" in str(exc)
    else:
        raise AssertionError("Secret-bearing project configuration must be rejected")


def _client() -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)
    settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        database_url="sqlite+pysqlite:///:memory:",
    )
    app = create_app_with_dependencies(
        settings=settings,
        rate_limiter=RedisFixedWindowRateLimiter(redis, key_prefix=settings.redis_key_prefix),
        redis_client=redis,
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app), session


def _seed_workspace(session: Session, *, role: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=f"{slug}@example.test", display_name=slug)
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    session.add_all([user, workspace, WorkspaceMember(workspace=workspace, user=user, role=role)])
    session.commit()
    return user, workspace


def _add_member(
    session: Session, workspace: Workspace, *, role: str
) -> tuple[User, WorkspaceMember]:
    user = User(email=f"{uuid4()}@example.test", display_name=role)
    member = WorkspaceMember(workspace=workspace, user=user, role=role)
    session.add_all([user, member])
    session.commit()
    return user, member


def _file(
    session: Session,
    workspace_id: object,
    user_id: object | None,
    filename: str,
) -> WorkspaceFile:
    file = WorkspaceFile(
        workspace_id=workspace_id,
        uploaded_by_user_id=user_id,
        filename=filename,
        content_type="text/plain",
        size_bytes=1,
        checksum_sha256="a" * 64,
        storage_key=f"workspaces/{workspace_id}/files/{uuid4()}/{filename}",
    )
    session.add(file)
    session.commit()
    return file


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
