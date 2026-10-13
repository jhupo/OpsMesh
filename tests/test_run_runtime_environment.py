"""Native sandbox product references: ownership, encrypted state and scoped cleanup."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents.sandbox import Manifest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.sandboxes.service import SandboxService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import ConflictError, NotFoundError, PolicyDeniedError
from tests.fixtures.agent_product import _seed_workspace, _session
from tests.fixtures.native_sandbox import native_boundary


def fixture():
    session = _session()
    _, workspace = _seed_workspace(session, with_default_provider=False)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Native",
        status="active",
        sandbox_backend_id="docker",
        sandbox_options={"image": "python:3.13"},
    )
    session.add(runtime)
    session.commit()
    native, client, registry = native_boundary()
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), Settings(environment="test")
    )
    service = SandboxService(services.transaction, registry, services.secrets())
    return SimpleNamespace(
        session=session,
        workspace=workspace,
        runtime=runtime,
        native=native,
        client=client,
        service=service,
    )


def test_native_owner_reuse_and_scoped_delete_preserve_other_execution():
    f = fixture()

    async def exercise():
        owner = uuid4()
        first = await f.service.create(
            workspace_id=f.workspace.id,
            runtime_id=f.runtime.id,
            owner_kind="run",
            owner_id=owner,
            manifest=Manifest(root="/workspace"),
        )
        second = await f.service.create(
            workspace_id=f.workspace.id,
            runtime_id=f.runtime.id,
            owner_kind="run",
            owner_id=uuid4(),
            manifest=Manifest(root="/workspace"),
        )
        reused = await f.service.create(
            workspace_id=f.workspace.id,
            runtime_id=f.runtime.id,
            owner_kind="run",
            owner_id=owner,
            manifest=Manifest(root="/workspace"),
        )
        assert first.id == reused.id and first.id != second.id
        with pytest.raises(NotFoundError):
            await f.service.resume(uuid4(), first.id)
        await f.service.delete(f.workspace.id, first.id)
        await f.service.delete(f.workspace.id, first.id)
        assert f.client.delete.await_count == 1
        f.session.expire_all()
        remaining = f.session.get(SandboxReference, second.id)
        assert remaining.status == "running"
        assert (
            remaining.encrypted_session_state
            and "native-fixture" not in remaining.encrypted_session_state
        )
        await f.service.delete(f.workspace.id, second.id)

    asyncio.run(exercise())


def test_unknown_create_outcome_is_not_silently_retried():
    f = fixture()
    f.client.create.side_effect = TimeoutError("native outcome unknown")
    owner = uuid4()

    async def exercise():
        with pytest.raises(TimeoutError):
            await f.service.create(
                workspace_id=f.workspace.id,
                runtime_id=f.runtime.id,
                owner_kind="run",
                owner_id=owner,
                manifest=Manifest(root="/workspace"),
            )
        with pytest.raises(ConflictError):
            await f.service.create(
                workspace_id=f.workspace.id,
                runtime_id=f.runtime.id,
                owner_kind="run",
                owner_id=owner,
                manifest=Manifest(root="/workspace"),
            )
        assert f.client.create.await_count == 1

    asyncio.run(exercise())
    assert f.session.scalar(select(SandboxReference)).status == "unknown"


def test_cancellation_waits_for_native_delete_and_commits_terminal_reference():
    f = fixture()

    async def exercise():
        reference = await f.service.create(
            workspace_id=f.workspace.id,
            runtime_id=f.runtime.id,
            owner_kind="run",
            owner_id=uuid4(),
            manifest=Manifest(root="/workspace"),
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def delete(native):
            entered.set()
            await release.wait()

        f.client.delete.side_effect = delete
        pending = asyncio.create_task(f.service.delete(f.workspace.id, reference.id))
        await entered.wait()
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        f.session.expire_all()
        assert f.session.get(SandboxReference, reference.id).status == "deleted"

    asyncio.run(exercise())


def test_failed_resume_deletes_only_replacement_container_with_the_same_session_id():
    f = fixture()

    async def exercise():
        reference = await f.service.create(
            workspace_id=f.workspace.id,
            runtime_id=f.runtime.id,
            owner_kind="run",
            owner_id=uuid4(),
            manifest=Manifest(root="/workspace"),
        )
        replacement, _, _ = native_boundary()
        replacement.state = f.native.state.model_copy(update={"container_id": "replacement"})
        replacement.start.side_effect = RuntimeError("native restore failed")
        f.client.resume.return_value = replacement
        with pytest.raises(RuntimeError, match="native restore failed"):
            await f.service.resume(f.workspace.id, reference.id)
        f.client.delete.assert_awaited_once_with(replacement)
        assert replacement.state.session_id == f.native.state.session_id
        assert f.native.state.container_id == "native-fixture"

    asyncio.run(exercise())


@pytest.mark.parametrize("path", ["../outside", "/absolute", "folder\\escape"])
def test_native_file_path_denial(path):
    f = fixture()
    with pytest.raises(PolicyDeniedError):
        f.service._workspace_path(f.native, path)
