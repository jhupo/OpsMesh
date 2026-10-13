"""Tenant scope at public SDK snapshot and project-extension boundaries."""

import asyncio
import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from temporalio.testing import ActivityEnvironment

from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.sandboxes.snapshots import SandboxSnapshotStorage
from opsmesh.shared.config import Settings
from opsmesh.workspaces.projects.io.native import require_workflow_scope


def test_native_snapshot_is_tenant_scoped_and_survives_run_changes(tmp_path: Path) -> None:
    workspace_id, run_id, snapshot_id = uuid4(), uuid4(), str(uuid4())
    storage = SandboxSnapshotStorage(LocalObjectStorage(str(tmp_path)))
    owner = ActivityEnvironment()
    owner.info = replace(owner.info, workflow_id=f"opsmesh:{workspace_id}:agent:{run_id}")
    asyncio.run(owner.run(storage.upload, snapshot_id, io.BytesIO(b"native snapshot")))
    assert asyncio.run(owner.run(storage.exists, snapshot_id))

    foreign = ActivityEnvironment()
    foreign.info = replace(foreign.info, workflow_id=f"opsmesh:{uuid4()}:agent:{run_id}")
    assert not asyncio.run(foreign.run(storage.exists, snapshot_id))
    with pytest.raises(FileNotFoundError):
        asyncio.run(foreign.run(storage.download, snapshot_id))

    # The SDK session resumes in subsequent Runs. The admission interceptor,
    # rather than this object-key adapter, enforces session ownership per Run.
    following_turn = ActivityEnvironment()
    following_turn.info = replace(
        following_turn.info, workflow_id=f"opsmesh:{workspace_id}:agent:{uuid4()}"
    )
    assert asyncio.run(following_turn.run(storage.exists, snapshot_id))
    with asyncio.run(following_turn.run(storage.download, snapshot_id)) as stream:
        assert stream.read() == b"native snapshot"


def test_native_project_extension_rejects_forged_workflow_identity() -> None:
    environment = ActivityEnvironment()
    workspace_id, run_id = uuid4(), uuid4()
    environment.info = replace(environment.info, workflow_id=f"opsmesh:{uuid4()}:agent:{run_id}")
    with pytest.raises(ValueError, match="authorized Agent workflow"):
        environment.run(require_workflow_scope, workspace_id, run_id)


def test_native_registry_closes_owned_docker_client_once_and_preserves_injected_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = Mock()
    monkeypatch.setattr("opsmesh.runtime.sandboxes.factory.docker.from_env", lambda **_: created)
    settings = Settings(environment="test")
    with build_sandbox_backends(settings) as owned:
        assert owned.require("docker").client.backend_id == "docker"
    owned.close()
    created.close.assert_called_once()
    injected = Mock()
    with build_sandbox_backends(settings, injected) as borrowed:
        assert borrowed.require("docker").client.backend_id == "docker"
    injected.close.assert_not_called()


@pytest.mark.parametrize("snapshot_id", ["../secret", "/tmp/snapshot", "not-a-uuid"])
def test_native_snapshot_rejects_unsafe_storage_identity(tmp_path: Path, snapshot_id: str) -> None:
    storage = SandboxSnapshotStorage(LocalObjectStorage(str(tmp_path)))
    environment = ActivityEnvironment()
    environment.info = replace(environment.info, workflow_id=f"opsmesh:{uuid4()}:agent:{uuid4()}")
    with pytest.raises(ValueError):
        asyncio.run(environment.run(storage.exists, snapshot_id))
