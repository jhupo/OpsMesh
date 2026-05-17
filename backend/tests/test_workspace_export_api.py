import json
from collections.abc import Generator
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.dependencies import get_redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.tasks.models import Task
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.queue import RedisQueue
from backend.app.workers.runner import WorkerRunner, WorkerRunnerConfig
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_workspace_metadata_export_is_scoped_and_audited(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner-space")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other-space")
    agent = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    other_agent = AgentProfile(workspace_id=other_workspace.id, name="Other", role="researcher")
    team = AgentTeam(workspace_id=workspace.id, name="Research Team", team_type="research")
    task = Task(workspace_id=workspace.id, created_by_user_id=owner.id, title="Q2 Research")
    session.add_all([agent, other_agent, team, task])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="researcher",
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/metadata",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "filename=\"owner-space-workspace-export.json\"" in response.headers[
        "content-disposition"
    ]
    payload = json.loads(response.content)
    assert payload["manifest"]["format_version"] == "workspace-export.v1"
    assert payload["workspace"]["id"] == str(workspace.id)
    assert payload["manifest"]["counts"]["agents"] == 1
    assert payload["manifest"]["counts"]["teams"] == 1
    assert payload["manifest"]["counts"]["team_members"] == 1
    assert payload["manifest"]["counts"]["tasks"] == 1
    assert payload["agents"][0]["id"] == str(agent.id)
    assert all(item["workspace_id"] == str(workspace.id) for item in payload["agents"])
    assert other_agent.id not in {item["id"] for item in payload["agents"]}

    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "workspace.export.created",
        )
    )
    assert audit is not None
    assert audit.user_id == owner.id


def test_workspace_metadata_export_denies_cross_workspace_access(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    response = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/exports/metadata",
        headers=_headers(owner.id),
        json={},
    )

    assert response.status_code == 403
    assert workspace.id != other_workspace.id


def test_workspace_metadata_import_supports_dry_run_and_committed_import(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    agent = AgentProfile(workspace_id=source_workspace.id, name="Researcher", role="researcher")
    team = AgentTeam(workspace_id=source_workspace.id, name="Research Team", team_type="research")
    task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Q2 Research",
        domain_type="research",
    )
    session.add_all([agent, team, task])
    session.flush()
    session.add(
        AgentTeamMember(
            workspace_id=source_workspace.id,
            agent_team_id=team.id,
            agent_profile_id=agent.id,
            team_role="researcher",
        )
    )
    session.commit()
    export_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False, "include_runs": False, "include_files": False},
    )
    export_payload = json.loads(export_response.content)

    dry_run = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": True},
    )
    after_dry_run_agents = session.scalars(
        select(AgentProfile).where(AgentProfile.workspace_id == target_workspace.id)
    ).all()
    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/metadata/import",
        headers=_headers(target_user.id),
        json={"export": export_payload, "dry_run": False},
    )

    assert dry_run.status_code == 200
    assert dry_run.json()["created_counts"]["agents"] == 1
    assert after_dry_run_agents == []
    assert committed.status_code == 200
    body = committed.json()
    assert body["dry_run"] is False
    assert body["created_counts"]["agents"] == 1
    assert body["created_counts"]["teams"] == 1
    assert body["created_counts"]["team_members"] == 1
    assert body["created_counts"]["tasks"] == 1
    assert len(body["id_map"]["agents"]) == 1

    imported_agent = session.scalar(
        select(AgentProfile).where(
            AgentProfile.workspace_id == target_workspace.id,
            AgentProfile.name == "Imported Researcher",
        )
    )
    imported_team = session.scalar(
        select(AgentTeam).where(
            AgentTeam.workspace_id == target_workspace.id,
            AgentTeam.name == "Imported Research Team",
        )
    )
    imported_task = session.scalar(
        select(Task).where(
            Task.workspace_id == target_workspace.id,
            Task.title == "Imported Q2 Research",
        )
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.import.created",
        )
    )
    assert imported_agent is not None
    assert imported_team is not None
    assert imported_task is not None
    assert imported_task.status == "draft"
    assert audit is not None
    assert audit.user_id == target_user.id


def test_workspace_archive_export_includes_metadata_and_file_bytes(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"hello archive", "text/plain")},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "metadata.json" in names
        file_name = f"files/{file_id}/brief.txt"
        assert file_name in names
        assert archive.read(file_name) == b"hello archive"
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["manifest"]["counts"]["files"] == 1


def test_workspace_archive_export_skips_large_objects(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("large.txt", b"1234567890", "text/plain")},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["id"]

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive",
        headers=_headers(owner.id),
        json={"max_bytes_per_object": 3},
    )

    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert f"files/{file_id}/large.txt" not in names
        assert "skipped-objects.json" in names
        skipped = json.loads(archive.read("skipped-objects.json"))
        assert any("exceeds max_bytes_per_object" in item for item in skipped)


def test_workspace_archive_import_restores_metadata_and_file_bytes(tmp_path: Path) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    uploaded = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/files",
        headers=_headers(source_user.id),
        files={"file": ("brief.txt", b"portable data", "text/plain")},
    )
    assert uploaded.status_code == 201
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )

    dry_run = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "true"},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["created_counts"]["files"] == 1
    assert session.scalars(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    ).all() == []

    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "false"},
    )
    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["files"] == 1
    imported_file = session.scalar(
        select(WorkspaceFile).where(WorkspaceFile.workspace_id == target_workspace.id)
    )
    assert imported_file is not None
    assert imported_file.filename == "Imported brief.txt"
    downloaded = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/files/{imported_file.id}/download",
        headers=_headers(target_user.id),
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == target_workspace.id,
            AuditEvent.action == "workspace.archive_import.created",
        )
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"portable data"
    assert audit is not None


def test_workspace_archive_import_restores_artifact_bytes_and_task_mapping(
    tmp_path: Path,
) -> None:
    client, session = _client(tmp_path)
    source_user, source_workspace = _seed_workspace(
        session,
        email="source@example.com",
        slug="source",
    )
    target_user, target_workspace = _seed_workspace(
        session,
        email="target@example.com",
        slug="target",
    )
    source_task = Task(
        workspace_id=source_workspace.id,
        created_by_user_id=source_user.id,
        title="Novel Draft",
        domain_type="writing",
    )
    session.add(source_task)
    session.flush()
    artifact_bytes = b"chapter one artifact"
    storage_key = f"workspaces/{source_workspace.id}/artifacts/{source_task.id}/chapter.txt"
    LocalStorage(str(tmp_path)).write(storage_key, artifact_bytes)
    source_artifact = Artifact(
        workspace_id=source_workspace.id,
        task_id=source_task.id,
        agent_run_id=None,
        artifact_type="document",
        filename="chapter.txt",
        content_type="text/plain",
        size_bytes=len(artifact_bytes),
        checksum_sha256=sha256(artifact_bytes).hexdigest(),
        storage_key=storage_key,
        artifact_metadata={"stage": "draft"},
        created_at=datetime.now(UTC),
    )
    session.add(source_artifact)
    session.commit()
    archive_response = client.post(
        f"/api/v1/workspaces/{source_workspace.id}/exports/archive",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )

    committed = client.post(
        f"/api/v1/workspaces/{target_workspace.id}/exports/archive/import",
        headers=_headers(target_user.id),
        files={"file": ("archive.zip", archive_response.content, "application/zip")},
        data={"dry_run": "false"},
    )

    assert committed.status_code == 200
    body = committed.json()
    assert body["created_counts"]["tasks"] == 1
    assert body["created_counts"]["artifacts"] == 1
    imported_task = session.scalar(
        select(Task).where(
            Task.workspace_id == target_workspace.id,
            Task.title == "Imported Novel Draft",
        )
    )
    imported_artifact = session.scalar(
        select(Artifact).where(
            Artifact.workspace_id == target_workspace.id,
            Artifact.filename == "Imported chapter.txt",
        )
    )
    assert imported_task is not None
    assert imported_artifact is not None
    assert imported_artifact.task_id == imported_task.id
    assert imported_artifact.agent_run_id is None
    assert imported_artifact.checksum_sha256 == sha256(artifact_bytes).hexdigest()
    assert imported_artifact.artifact_metadata["imported_from_artifact_id"] == str(
        source_artifact.id
    )
    downloaded = client.get(
        f"/api/v1/workspaces/{target_workspace.id}/artifacts/"
        f"{imported_artifact.id}/download",
        headers=_headers(target_user.id),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == artifact_bytes


def test_workspace_archive_export_job_runs_in_worker_and_downloads_zip(
    tmp_path: Path,
) -> None:
    client, session, session_factory, queue = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace.id}/files",
        headers=_headers(owner.id),
        files={"file": ("brief.txt", b"async archive", "text/plain")},
    )
    assert uploaded.status_code == 201

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_audit_events": False},
    )

    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="export-worker", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
        ),
    )
    assert runner.run_once() is True
    status_response = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}",
        headers=_headers(owner.id),
    )
    download = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/{job_id}/download",
        headers=_headers(owner.id),
    )

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "completed"
    assert status_body["size_bytes"] > 0
    assert status_body["checksum_sha256"]
    assert download.status_code == 200
    with ZipFile(BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        file_name = f"files/{uploaded.json()['id']}/brief.txt"
        assert "metadata.json" in names
        assert archive.read(file_name) == b"async archive"


def test_workspace_archive_export_job_download_requires_completion(tmp_path: Path) -> None:
    client, session, _, _ = _client_with_worker_queue(tmp_path)
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs",
        headers=_headers(owner.id),
        json={"include_file_bytes": False, "include_artifact_bytes": False},
    )
    assert created.status_code == 202

    download = client.get(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/jobs/"
        f"{created.json()['id']}/download",
        headers=_headers(owner.id),
    )

    assert download.status_code == 409


def _client(tmp_path: Path) -> tuple[TestClient, Session]:
    client, session, _, _ = _client_with_worker_queue(tmp_path)
    return client, session


def _client_with_worker_queue(
    tmp_path: Path,
) -> tuple[TestClient, Session, sessionmaker[Session], RedisQueue]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis, RedisKeyBuilder("chaincloud"), "agent_runs", 0)
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            storage_root=str(tmp_path),
            max_upload_bytes=1024,
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
    app.dependency_overrides[get_redis_client] = lambda: redis
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session, session_factory, queue


def _seed_workspace(session: Session, *, email: str, slug: str) -> tuple[User, Workspace]:
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
