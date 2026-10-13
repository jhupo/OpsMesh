"""Real PostgreSQL project grants and native SDK file extension behavior."""

import asyncio
import io
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from agents.sandbox import Manifest
from agents.sandbox.files import EntryKind, FileEntry
from agents.sandbox.types import ExecResult, Permissions
from sqlalchemy import select
from temporalio.testing import ActivityEnvironment

from opsmesh.capabilities.references.models import CapabilityResource
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.workspaces.projects.io.native import (
    ProjectInputEntry,
    ProjectOutputActivities,
    ProjectOutputHarvestRequest,
    ProjectSandboxFileService,
)
from opsmesh.workspaces.projects.io.support import RunProjectIOError
from opsmesh.workspaces.projects.models import AgentRunProjectIOState
from opsmesh.workspaces.projects.snapshots.diff import sha256_json
from opsmesh.workspaces.projects.snapshots.service import RunProjectSnapshotService
from tests.fixtures.native_sandbox import native_boundary
from tests.fixtures.project_product import project_fixture


def boundary(f):
    native, client, registry = native_boundary(
        Manifest(
            root="/workspace",
            entries={
                ".opsmesh/project": ProjectInputEntry(workspace_id=f.workspace.id, run_id=f.run.id)
            },
        )
    )
    files = {}

    async def write(path, stream):
        files[str(path)] = stream.read()

    async def read(path):
        return io.BytesIO(files[str(path)])

    async def execute(*command, **kwargs):
        assert command[:2] == ("test", "-e")
        return ExecResult(stdout=b"", stderr=b"", exit_code=0 if str(command[2]) in files else 1)

    async def listing(path):
        content = files[str(path)]
        return [
            FileEntry(
                path=str(path),
                permissions=Permissions(),
                owner="test",
                group="test",
                size=len(content),
                kind=EntryKind.FILE,
            )
        ]

    native.write.side_effect = write
    native.read.side_effect = read
    native.exec.side_effect = execute
    native.ls.side_effect = listing
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=f"opsmesh:{f.workspace.id}:agent:{f.run.id}"
    )
    staging = ProjectSandboxFileService(f.services, f.storage)
    harvest = ProjectOutputActivities(f.services, registry, f.storage)
    operation = ProjectOutputHarvestRequest(
        workspace_id=f.workspace.id,
        run_id=f.run.id,
        backend_id="docker",
        session_state=native.state.model_dump(mode="json"),
    )
    return native, files, environment, staging, harvest, operation


def test_project_inputs_outputs_and_repeated_harvest_are_versioned_and_audited(tmp_path):
    f = project_fixture(tmp_path)
    native, files, environment, staging, harvest, operation = boundary(f)
    asyncio.run(environment.run(staging.stage, native, f.workspace.id, f.run.id))
    assert files[str(Path("/workspace/inputs/spec.txt"))] == b"project input"
    files[str(Path("/workspace/outputs/report.json"))] = b'{"result": "ok"}'
    asyncio.run(environment.run(harvest.harvest, operation))
    asyncio.run(environment.run(harvest.harvest, operation))
    f.session.expire_all()
    artifacts = f.session.scalars(select(Artifact).where(Artifact.agent_run_id == f.run.id)).all()
    assert len(artifacts) == 1
    with f.storage.open(artifacts[0].storage_key) as stream:
        assert stream.read() == b'{"result": "ok"}'
    assert f.session.scalar(select(AgentRunProjectIOState)).status == "harvested"
    assert (
        f.session.scalar(select(AuditEvent).where(AuditEvent.action == "project.outputs_harvested"))
        is not None
    )


@pytest.mark.parametrize(
    "denial", ["corrupt_storage", "sensitive_file", "revoked_grant", "tampered_path"]
)
def test_input_denials_precede_sdk_write_and_leave_scoped_evidence(tmp_path, denial):
    f = project_fixture(tmp_path)
    if denial == "corrupt_storage":
        f.storage.write(f.source_file.storage_key, b"corrupt")
    elif denial == "sensitive_file":
        f.source_file.runtime_access = "denied"
    elif denial == "revoked_grant":
        resource = f.session.scalar(
            select(CapabilityResource).where(CapabilityResource.resource_type == "file_collection")
        )
        resource.version += 1
    else:
        snapshot = RunProjectSnapshotService(f.session).get_for_run(f.workspace.id, f.run.id)
        manifest = deepcopy(snapshot.manifest)
        manifest["files"][0]["project_path"] = "../outside-canary"
        snapshot.manifest = manifest
        snapshot.fingerprint_sha256 = sha256_json(manifest)
        payload = deepcopy(f.run.input)
        payload["project_snapshot"]["fingerprint_sha256"] = snapshot.fingerprint_sha256
        f.run.input = payload
    f.session.commit()
    native, files, environment, staging, _, _ = boundary(f)
    with pytest.raises(RunProjectIOError):
        asyncio.run(environment.run(staging.stage, native, f.workspace.id, f.run.id))
    assert not files and native.write.await_count == 0
    f.session.expire_all()
    state = f.session.scalar(select(AgentRunProjectIOState))
    assert state.status == "failed"
    events = f.session.scalars(select(AuditEvent)).all()
    assert events and "outside-canary" not in json.dumps([item.audit_metadata for item in events])
    if denial != "corrupt_storage":
        assert f.session.scalar(select(SecurityEvent)) is not None


@pytest.mark.parametrize(
    "denial", ["missing_output", "too_large", "revoked_file", "foreign_workflow"]
)
def test_output_denials_prevent_artifact_creation(tmp_path, denial):
    f = project_fixture(tmp_path)
    native, files, environment, staging, harvest, operation = boundary(f)
    asyncio.run(environment.run(staging.stage, native, f.workspace.id, f.run.id))
    if denial != "missing_output":
        files[str(Path("/workspace/outputs/report.json"))] = b"x" * (
            1025 if denial == "too_large" else 1
        )
    if denial == "revoked_file":
        f.source_file.runtime_access = "denied"
        f.session.commit()
    if denial == "foreign_workflow":
        environment.info = replace(
            environment.info, workflow_id=f"opsmesh:{uuid4()}:agent:{f.run.id}"
        )
    with pytest.raises((RunProjectIOError, ValueError)):
        asyncio.run(environment.run(harvest.harvest, operation))
    assert f.session.scalar(select(Artifact)) is None
