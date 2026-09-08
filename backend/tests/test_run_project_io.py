from __future__ import annotations

import io
import json
import tarfile
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import CapabilityResource
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.projects.models import (
    AgentRunProjectIOState,
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.projects.run_snapshots import RunProjectSnapshotService
from backend.app.projects.runtime_io import RunProjectIOService
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.projects.serialization import sha256_json
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task
from backend.app.workspaces.models import Workspace, WorkspaceMember
from backend.tests.fixtures.project_authorization import authorize_project_run


@dataclass
class FakeDockerClient:
    staged_files: dict[str, bytes] = field(default_factory=dict)
    staged_modes: dict[str, int] = field(default_factory=dict)
    output_files: dict[str, bytes] = field(default_factory=dict)

    def copy_archive_to_container(
        self,
        container_id: str,
        destination_path: str,
        archive: bytes,
        timeout_seconds: int,
    ) -> None:
        assert container_id == "container-1"
        assert destination_path == "/workspace"
        assert timeout_seconds == 60
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle.getmembers():
                self.staged_modes[member.name] = member.mode
                if not member.isfile():
                    continue
                stream = bundle.extractfile(member)
                assert stream is not None
                self.staged_files[member.name] = stream.read()

    def copy_file_from_container(
        self,
        container_id: str,
        source_path: str,
        max_bytes: int,
        timeout_seconds: int,
    ) -> bytes | None:
        assert container_id == "container-1"
        assert timeout_seconds == 60
        content = self.output_files.get(source_path)
        if content is not None and len(content) > max_bytes:
            raise ValueError("output too large")
        return content


@dataclass(frozen=True)
class ProjectIOFixture:
    session: Session
    storage: LocalStorage
    docker: FakeDockerClient
    settings: Settings
    owner: User
    workspace: Workspace
    project: WorkspaceProject
    output: WorkspaceProjectOutput
    source_file: WorkspaceFile
    run: AgentRun


def test_managed_runtime_stages_snapshot_and_versions_declared_outputs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = RunProjectIOService(
        fixture.session,
        fixture.storage,
        fixture.docker,
        fixture.settings,
    )

    staged = service.stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert staged is not None
    prefix = f"runs/{fixture.run.id}"
    input_path = f"{prefix}/inputs/spec.txt"
    contract_path = f"{prefix}/.opsmesh/project.json"
    assert fixture.docker.staged_files[input_path] == b"project input"
    assert fixture.docker.staged_modes[input_path] == 0o444
    contract = json.loads(fixture.docker.staged_files[contract_path])
    assert contract["fingerprint_sha256"] == fixture.run.input["project_snapshot"][
        "fingerprint_sha256"
    ]
    assert "storage_key" not in json.dumps(contract)

    first_content = b'{"status":"ok"}'
    output_runtime_path = f"/workspace/{prefix}/outputs/report.json"
    fixture.docker.output_files[output_runtime_path] = first_content
    first = service.harvest_outputs(fixture.run, actor_user_id=fixture.owner.id)
    repeated = service.harvest_outputs(fixture.run, actor_user_id=fixture.owner.id)

    assert len(first) == 1
    assert [artifact.id for artifact in repeated] == [first[0].id]
    assert first[0].version == 1
    assert first[0].workspace_project_id == fixture.project.id
    assert first[0].workspace_project_output_id == fixture.output.id
    assert first[0].project_path == "outputs/report.json"
    assert fixture.storage.read(first[0].storage_key) == first_content

    second_run = _new_run(fixture)
    second_service = RunProjectIOService(
        fixture.session,
        fixture.storage,
        fixture.docker,
        fixture.settings,
    )
    second_service.stage_inputs(second_run, actor_user_id=fixture.owner.id)
    second_content = b'{"status":"updated"}'
    fixture.docker.output_files[
        f"/workspace/runs/{second_run.id}/outputs/report.json"
    ] = second_content
    second = second_service.harvest_outputs(second_run, actor_user_id=fixture.owner.id)

    assert second[0].version == 2
    assert second[0].supersedes_artifact_id == first[0].id
    assert fixture.storage.read(second[0].storage_key) == second_content
    states = fixture.session.scalars(
        select(AgentRunProjectIOState).order_by(AgentRunProjectIOState.created_at)
    ).all()
    assert [state.status for state in states] == ["harvested", "harvested"]
    assert [state.staged_file_count for state in states] == [1, 1]
    actions = set(fixture.session.scalars(select(AuditEvent.action)).all())
    assert {"project.inputs_staged", "project.outputs_harvested"} <= actions
    access_actions = set(fixture.session.scalars(select(FileAccessEvent.action)).all())
    assert {"stage_to_runtime", "collect_from_runtime"} <= access_actions


def test_managed_runtime_fails_closed_when_required_output_is_missing(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = RunProjectIOService(
        fixture.session,
        fixture.storage,
        fixture.docker,
        fixture.settings,
    )
    service.stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    with pytest.raises(ProjectRunIOError) as failure:
        service.harvest_outputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_required_output_missing"
    state = fixture.session.scalar(
        select(AgentRunProjectIOState).where(
            AgentRunProjectIOState.agent_run_id == fixture.run.id
        )
    )
    assert state is not None
    assert state.status == "failed"
    assert state.error == {
        "code": "project_required_output_missing",
        "message": "A required project output was not produced",
        "retryable": False,
    }
    event = fixture.session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == fixture.run.id,
            RunEvent.event_type == "project.output_harvest.failed",
        )
    )
    assert event is not None
    assert event.event_metadata["project_output_id"] == str(fixture.output.id)


def test_managed_runtime_rejects_corrupt_snapshotted_input(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.storage.write(fixture.source_file.storage_key, b"tampered data")
    service = RunProjectIOService(
        fixture.session,
        fixture.storage,
        fixture.docker,
        fixture.settings,
    )

    with pytest.raises(ProjectRunIOError) as failure:
        service.stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_input_integrity_failed"
    assert fixture.docker.staged_files == {}
    state = fixture.session.scalar(
        select(AgentRunProjectIOState).where(
            AgentRunProjectIOState.agent_run_id == fixture.run.id
        )
    )
    assert state is not None
    assert state.status == "failed"


def test_project_snapshot_without_runtime_fails_instead_of_skipping_io(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.run.runtime_id = None
    fixture.session.commit()

    with pytest.raises(ProjectRunIOError) as failure:
        RunProjectIOService(
            fixture.session,
            fixture.storage,
            fixture.docker,
            fixture.settings,
        ).stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_runtime_required"
    assert fixture.docker.staged_files == {}


def test_project_scope_denial_is_fail_closed_and_audited_without_file_details(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    run_input = deepcopy(fixture.run.input)
    authorization = run_input["authorization_snapshot"]
    assert isinstance(authorization, dict)
    file_scope = authorization["file_scope"]
    runtime_binding = authorization["runtime_binding"]
    assert isinstance(file_scope, dict)
    assert isinstance(runtime_binding, dict)
    binding_scope = runtime_binding["file_access_scope"]
    assert isinstance(binding_scope, dict)
    file_scope["allowed_file_ids"] = []
    binding_scope["allowed_file_ids"] = []
    authorization["fingerprint"] = authorization_snapshot_fingerprint(authorization)
    fixture.run.input = run_input
    fixture.session.commit()

    with pytest.raises(ProjectRunIOError) as failure:
        RunProjectIOService(
            fixture.session,
            fixture.storage,
            fixture.docker,
            fixture.settings,
        ).stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_input_grant_denied"
    assert fixture.docker.staged_files == {}
    audit = fixture.session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == fixture.workspace.id,
            AuditEvent.action == "project.input_staging.failed",
        )
    )
    security = fixture.session.scalar(
        select(SecurityEvent).where(
            SecurityEvent.workspace_id == fixture.workspace.id,
            SecurityEvent.action == "project.file_boundary_blocked",
        )
    )
    assert audit is not None
    assert audit.audit_metadata["code"] == "project_input_grant_denied"
    assert security is not None
    assert security.reason == "project_input_grant_denied"
    evidence = json.dumps(
        {"audit": audit.audit_metadata, "security": security.event_metadata},
        sort_keys=True,
    )
    assert fixture.source_file.filename not in evidence
    assert fixture.source_file.storage_key not in evidence


@pytest.mark.parametrize(
    ("sensitivity", "runtime_access", "expected_code"),
    [
        ("restricted", "denied", "project_input_runtime_access_denied"),
        ("internal", "allowed", "project_input_sensitive_file_denied"),
    ],
)
def test_sensitive_project_files_are_denied_before_storage_read(
    tmp_path: Path,
    sensitivity: str,
    runtime_access: str,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.source_file.sensitivity = sensitivity
    fixture.source_file.runtime_access = runtime_access
    if expected_code == "project_input_sensitive_file_denied":
        fixture.source_file.filename = ".env.production"
    fixture.session.commit()

    with pytest.raises(ProjectRunIOError) as failure:
        RunProjectIOService(
            fixture.session,
            fixture.storage,
            fixture.docker,
            fixture.settings,
        ).stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == expected_code
    assert fixture.docker.staged_files == {}


def test_file_policy_revocation_blocks_output_harvest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = RunProjectIOService(
        fixture.session,
        fixture.storage,
        fixture.docker,
        fixture.settings,
    )
    service.stage_inputs(fixture.run, actor_user_id=fixture.owner.id)
    fixture.source_file.runtime_access = "denied"
    fixture.session.commit()
    fixture.docker.output_files[
        f"/workspace/runs/{fixture.run.id}/outputs/report.json"
    ] = b'{}'

    with pytest.raises(ProjectRunIOError) as failure:
        service.harvest_outputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_input_runtime_access_denied"
    assert (
        fixture.session.scalar(
            select(Artifact).where(Artifact.agent_run_id == fixture.run.id)
        )
        is None
    )


def test_project_runtime_capacity_is_enforced_before_storage_read(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    runtime = fixture.session.get(WorkspaceRuntime, fixture.run.runtime_id)
    assert runtime is not None
    runtime.limits = {"disk_mb": 1}
    snapshot = RunProjectSnapshotService(fixture.session).get_for_run(
        fixture.workspace.id,
        fixture.run.id,
    )
    assert snapshot is not None
    manifest = deepcopy(snapshot.manifest)
    outputs = manifest["outputs"]
    assert isinstance(outputs, list)
    output = outputs[0]
    assert isinstance(output, dict)
    output["max_bytes"] = 2_000_000
    snapshot.manifest = manifest
    snapshot.fingerprint_sha256 = sha256_json(manifest)
    run_input = deepcopy(fixture.run.input)
    project_binding = run_input["project_snapshot"]
    assert isinstance(project_binding, dict)
    project_binding["fingerprint_sha256"] = snapshot.fingerprint_sha256
    fixture.run.input = run_input
    fixture.session.commit()

    with pytest.raises(ProjectRunIOError) as failure:
        RunProjectIOService(
            fixture.session,
            fixture.storage,
            fixture.docker,
            fixture.settings,
        ).stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_runtime_capacity_exceeded"
    assert fixture.docker.staged_files == {}


def test_changed_file_resource_grant_is_denied_and_audited(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    resource = fixture.session.scalar(
        select(CapabilityResource).where(
            CapabilityResource.workspace_id == fixture.workspace.id,
            CapabilityResource.resource_type == "file_collection",
        )
    )
    assert resource is not None
    resource.version += 1
    fixture.session.commit()

    with pytest.raises(ProjectRunIOError) as failure:
        RunProjectIOService(
            fixture.session,
            fixture.storage,
            fixture.docker,
            fixture.settings,
        ).stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_input_authorization_invalid"
    assert fixture.docker.staged_files == {}
    assert (
        fixture.session.scalar(
            select(SecurityEvent).where(
                SecurityEvent.workspace_id == fixture.workspace.id,
                SecurityEvent.reason == "project_input_authorization_invalid",
            )
        )
        is not None
    )


def test_tampered_project_path_is_rejected_with_redacted_security_evidence(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    snapshot = RunProjectSnapshotService(fixture.session).get_for_run(
        fixture.workspace.id,
        fixture.run.id,
    )
    assert snapshot is not None
    manifest = deepcopy(snapshot.manifest)
    files = manifest["files"]
    assert isinstance(files, list)
    project_file = files[0]
    assert isinstance(project_file, dict)
    project_file["project_path"] = "../outside.txt"
    snapshot.manifest = manifest
    snapshot.fingerprint_sha256 = sha256_json(manifest)
    run_input = deepcopy(fixture.run.input)
    project_binding = run_input["project_snapshot"]
    assert isinstance(project_binding, dict)
    project_binding["fingerprint_sha256"] = snapshot.fingerprint_sha256
    fixture.run.input = run_input
    fixture.session.commit()

    with pytest.raises(ProjectRunIOError) as failure:
        RunProjectIOService(
            fixture.session,
            fixture.storage,
            fixture.docker,
            fixture.settings,
        ).stage_inputs(fixture.run, actor_user_id=fixture.owner.id)

    assert failure.value.code == "project_snapshot_invalid"
    security = fixture.session.scalar(
        select(SecurityEvent).where(
            SecurityEvent.workspace_id == fixture.workspace.id,
            SecurityEvent.reason == "project_snapshot_invalid",
        )
    )
    assert security is not None
    assert "outside" not in json.dumps(security.event_metadata)
    assert fixture.docker.staged_files == {}


def _fixture(tmp_path: Path) -> ProjectIOFixture:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
        runtime_provider="docker",
        runtime_type="docker",
        name="managed-runtime",
        status="running",
        connection_status="online",
        docker_container_id="container-1",
        limits={"disk_mb": 256},
        capabilities={
            "isolation": {"workspace_mount": {"target": "/workspace", "mode": "rw"}}
        },
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
    storage = LocalStorage(str(tmp_path / "storage"))
    storage.write(storage_key, content)
    return ProjectIOFixture(
        session=session,
        storage=storage,
        docker=FakeDockerClient(),
        settings=Settings(
            environment="test",
            log_format="text",
            storage_root=str(tmp_path / "storage"),
        ),
        owner=owner,
        workspace=workspace,
        project=project,
        output=output,
        source_file=source_file,
        run=run,
    )


def _new_run(fixture: ProjectIOFixture) -> AgentRun:
    task = Task(
        workspace_id=fixture.workspace.id,
        created_by_user_id=fixture.owner.id,
        workspace_project_id=fixture.project.id,
        title="Produce another report",
        status="running",
    )
    fixture.session.add(task)
    fixture.session.flush()
    run = AgentRun(
        workspace_id=fixture.workspace.id,
        task_id=task.id,
        runtime_id=fixture.run.runtime_id,
        status="running",
        input={},
    )
    fixture.session.add(run)
    fixture.session.flush()
    runtime = fixture.session.get(WorkspaceRuntime, fixture.run.runtime_id)
    assert runtime is not None
    authorize_project_run(
        fixture.session,
        run=run,
        task=task,
        runtime=runtime,
        files=[fixture.source_file],
    )
    RunProjectSnapshotService(fixture.session).freeze_for_run(run=run, task=task)
    fixture.session.commit()
    return run


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
