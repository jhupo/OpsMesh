"""Project file extension fixture using real PostgreSQL and an explicit SDK I/O test double.

The test double covers OpsMesh governance; it does not certify Docker execution.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from opsmesh.workspaces.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from opsmesh.workspaces.projects.snapshots.diff import sha256_json
from opsmesh.workspaces.projects.snapshots.service import RunProjectSnapshotService
from tests.fixtures.database import product_engine
from tests.fixtures.project_authorization import authorize_project_run


@dataclass
class ProjectIOFixture:
    session: Session
    services: ActivityDependencies
    storage: LocalObjectStorage
    owner: User
    workspace: Workspace
    project: WorkspaceProject
    output: WorkspaceProjectOutput
    source_file: WorkspaceFile
    run: AgentRun


def project_fixture(tmp_path: Path) -> ProjectIOFixture:
    engine = product_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    owner = User(email="owner@example.test", display_name="owner")
    workspace = Workspace(owner=owner, name="Workspace", slug="workspace", settings={})
    session.add_all(
        [owner, workspace, WorkspaceMember(workspace=workspace, user=owner, role="owner")]
    )
    session.flush()

    content = b"project input"
    storage_key = f"workspaces/{workspace.id}/files/source/spec.txt"
    source_file = WorkspaceFile(
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
        name="Runtime project",
        slug="runtime-project",
        input_path="inputs",
        work_path="work",
        output_path="outputs",
        configuration={"language": "python"},
        configuration_version=1,
        status="active",
    )
    session.add_all([source_file, project])
    session.flush()
    configuration = WorkspaceProjectConfigurationVersion(
        workspace_id=workspace.id,
        project_id=project.id,
        version=1,
        configuration=project.configuration,
        checksum_sha256=sha256_json(project.configuration),
        created_by_user_id=owner.id,
        change_summary="Initial configuration",
    )
    binding = WorkspaceProjectFile(
        workspace_id=workspace.id,
        project_id=project.id,
        workspace_file_id=source_file.id,
        project_path="inputs/spec.txt",
        version=1,
        access_mode="read_only",
        status="active",
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
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Native",
        status="active",
        sandbox_backend_id="docker",
        sandbox_options={"image": "python:3.13"},
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        workspace_project_id=project.id,
        title="Produce report",
        status="running",
    )
    session.add_all([configuration, binding, output, runtime, task])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime.id,
        status="running",
        input={},
    )
    session.add(run)
    session.flush()
    authorize_project_run(
        session,
        run=run,
        task=task,
        runtime=runtime,
        files=[source_file],
    )
    RunProjectSnapshotService(session).freeze_for_run(run=run, task=task)
    session.commit()
    storage = LocalObjectStorage(str(tmp_path / "storage"))
    storage.write(storage_key, content)
    settings = Settings(environment="test", storage_root=str(tmp_path / "storage"))
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), settings
    )
    return ProjectIOFixture(
        session, services, storage, owner, workspace, project, output, source_file, run
    )
