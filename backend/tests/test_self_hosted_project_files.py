from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Generator
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.artifacts.models import Artifact
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.projects.models import (
    AgentRunProjectIOState,
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.projects.serialization import sha256_json
from backend.app.runs.models import AgentRun
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.tasks.models import Task
from backend.app.workspaces.models import Workspace, WorkspaceMember
from backend.tests.fixtures.project_authorization import authorize_project_run

TOKEN = "self-hosted-project-token"


def test_self_hosted_project_archive_and_declared_output_protocol(tmp_path: Path) -> None:
    client, session, storage = _client(tmp_path)
    owner, workspace = _seed_workspace(session)
    runtime_id, credential = _register_runtime(client, owner, workspace, "primary")
    _, foreign_credential = _register_runtime(client, owner, workspace, "other")
    run, output = _seed_project_run(session, storage, owner, workspace, runtime_id)

    claimed = client.post(
        f"/api/v1/self-hosted/jobs/{run.id}/claim",
        headers=_runtime_headers(credential),
    )

    assert claimed.status_code == 200
    contract = claimed.json()["project"]
    assert contract["root_path"] == f"runs/{run.id}"
    assert contract["manifest"]["outputs"][0]["project_output_id"] == str(output.id)
    assert "storage_key" not in json.dumps(contract)

    denied_archive = client.get(
        f"/api/v1/self-hosted/jobs/{run.id}/project/archive",
        headers=_runtime_headers(foreign_credential),
    )
    archive = client.get(
        f"/api/v1/self-hosted/jobs/{run.id}/project/archive",
        headers=_runtime_headers(credential),
    )

    assert denied_archive.status_code == 409
    assert archive.status_code == 200
    assert archive.headers["x-opsmesh-project-root"] == f"runs/{run.id}"
    with tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:") as bundle:
        input_member = bundle.extractfile(f"runs/{run.id}/inputs/spec.txt")
        manifest_member = bundle.extractfile(f"runs/{run.id}/.opsmesh/project.json")
        assert input_member is not None
        assert manifest_member is not None
        assert input_member.read() == b"self-hosted input"
        assert "storage_key" not in manifest_member.read().decode("utf-8")

    premature = client.post(
        f"/api/v1/self-hosted/jobs/{run.id}/complete",
        headers=_runtime_headers(credential),
        json={"status": "completed", "output": {"summary": "too early"}},
    )
    undeclared = client.put(
        f"/api/v1/self-hosted/jobs/{run.id}/project/outputs/{uuid4()}",
        headers={**_runtime_headers(credential), "Content-Type": "application/json"},
        content=b"{}",
    )
    oversized = client.put(
        f"/api/v1/self-hosted/jobs/{run.id}/project/outputs/{output.id}",
        headers={**_runtime_headers(credential), "Content-Type": "application/json"},
        content=b"x" * 1025,
    )
    wrong_type = client.put(
        f"/api/v1/self-hosted/jobs/{run.id}/project/outputs/{output.id}",
        headers={**_runtime_headers(credential), "Content-Type": "text/plain"},
        content=b"{}",
    )

    assert premature.status_code == 409
    assert "required project outputs" in premature.json()["error"]["message"]
    assert undeclared.status_code == 409
    assert oversized.status_code == 413
    assert wrong_type.status_code == 409

    content = b'{"status":"complete"}'
    uploaded = client.put(
        f"/api/v1/self-hosted/jobs/{run.id}/project/outputs/{output.id}",
        headers={**_runtime_headers(credential), "Content-Type": "application/json"},
        content=content,
    )
    repeated = client.put(
        f"/api/v1/self-hosted/jobs/{run.id}/project/outputs/{output.id}",
        headers={**_runtime_headers(credential), "Content-Type": "application/json"},
        content=content,
    )
    completed = client.post(
        f"/api/v1/self-hosted/jobs/{run.id}/complete",
        headers=_runtime_headers(credential),
        json={"status": "completed", "output": {"summary": "done"}},
    )

    assert uploaded.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["id"] == uploaded.json()["id"]
    assert completed.status_code == 200
    artifact = session.scalar(select(Artifact).where(Artifact.agent_run_id == run.id))
    assert artifact is not None
    assert artifact.workspace_project_output_id == output.id
    assert artifact.version == 1
    assert storage.read(artifact.storage_key) == content
    state = session.scalar(
        select(AgentRunProjectIOState).where(AgentRunProjectIOState.agent_run_id == run.id)
    )
    assert state is not None
    assert state.status == "harvested"
    assert state.staged_file_count == 1
    assert state.harvested_output_count == 1


def _client(tmp_path: Path) -> tuple[TestClient, Session, LocalStorage]:
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
    storage_root = tmp_path / "storage"
    settings = Settings(
        environment="test",
        log_format="text",
        internal_api_token=TOKEN,
        storage_root=str(storage_root),
    )
    app = create_app(settings)

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), session, LocalStorage(str(storage_root))


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    owner = User(email="owner@example.test", display_name="owner")
    workspace = Workspace(owner=owner, name="Workspace", slug="workspace", settings={})
    session.add_all(
        [owner, workspace, WorkspaceMember(workspace=workspace, user=owner, role="owner")]
    )
    session.commit()
    return owner, workspace


def _register_runtime(
    client: TestClient,
    owner: User,
    workspace: Workspace,
    suffix: str,
) -> tuple[UUID, str]:
    enrollment = client.post(
        f"/api/v1/workspaces/{workspace.id}/self-hosted/enrollment-tokens",
        headers=_headers(owner.id),
        json={"name": f"node-{suffix}"},
    )
    registered = client.post(
        "/api/v1/self-hosted/register",
        json={
            "enrollment_token": enrollment.json()["token"],
            "name": f"node-{suffix}",
            "machine_id": f"machine-{suffix}",
            "version": "2.0.0",
        },
    )
    return UUID(registered.json()["workspace_runtime_id"]), registered.json()[
        "credential_token"
    ]


def _seed_project_run(
    session: Session,
    storage: LocalStorage,
    owner: User,
    workspace: Workspace,
    runtime_id: UUID,
) -> tuple[AgentRun, WorkspaceProjectOutput]:
    content = b"self-hosted input"
    storage_key = f"workspaces/{workspace.id}/files/source/spec.txt"
    source = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=owner.id,
        filename="spec.txt",
        content_type="text/plain",
        size_bytes=len(content),
        checksum_sha256=sha256(content).hexdigest(),
        storage_key=storage_key,
        status="active",
    )
    project = WorkspaceProject(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        name="Self-hosted project",
        slug="self-hosted-project",
        input_path="inputs",
        work_path="work",
        output_path="outputs",
        configuration={"runtime": "self-hosted"},
        configuration_version=1,
        status="active",
    )
    session.add_all([source, project])
    session.flush()
    session.add_all(
        [
            WorkspaceProjectConfigurationVersion(
                workspace_id=workspace.id,
                project_id=project.id,
                version=1,
                configuration=project.configuration,
                checksum_sha256=sha256_json(project.configuration),
                created_by_user_id=owner.id,
                change_summary="Initial configuration",
            ),
            WorkspaceProjectFile(
                workspace_id=workspace.id,
                project_id=project.id,
                workspace_file_id=source.id,
                project_path="inputs/spec.txt",
                version=1,
                access_mode="read_only",
                status="active",
            ),
        ]
    )
    output = WorkspaceProjectOutput(
        workspace_id=workspace.id,
        project_id=project.id,
        project_path="outputs/report.json",
        artifact_type="report",
        content_type="application/json",
        required=True,
        max_bytes=1024,
        status="active",
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        workspace_project_id=project.id,
        title="Self-hosted report",
        status="queued",
    )
    session.add_all([output, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime_id,
        status="queued",
        input={},
    )
    session.add(run)
    session.flush()
    runtime = session.get(WorkspaceRuntime, runtime_id)
    assert runtime is not None
    authorize_project_run(
        session,
        run=run,
        task=task,
        runtime=runtime,
        files=[source],
    )
    RunProjectSnapshotService(session).freeze_for_run(run=run, task=task)
    session.commit()
    storage.write(storage_key, content)
    return run, output


def _headers(user_id: UUID) -> dict[str, str]:
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
