"""Command Activity cleanup and cancellation persist scoped terminal state."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents.sandbox.types import ExecResult
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from temporalio.testing import ActivityEnvironment

from opsmesh.runtime.instances.models import RuntimeCommand, WorkspaceRuntime
from opsmesh.runtime.sandboxes.activities import SandboxControlActivities
from opsmesh.runtime.sandboxes.service import SandboxReferenceSnapshot
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from tests.fixtures.agent_product import _seed_workspace, _session
from tests.fixtures.native_sandbox import native_boundary


@pytest.mark.parametrize(
    "outcome", ["success", "timeout", "nonzero", "cancelled", "cleanup_failure"]
)
def test_native_command_activity_terminal_result_and_scoped_cleanup(outcome):
    session = _session()
    user, workspace = _seed_workspace(session, with_default_provider=False)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Native",
        status="active",
        sandbox_backend_id="docker",
        sandbox_options={"image": "python:3.13"},
    )
    session.add(runtime)
    session.flush()
    command = RuntimeCommand(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        requested_by_user_id=user.id,
        command=["python", "--version"],
        status="queued",
    )
    session.add(command)
    session.commit()
    native, _, registry = native_boundary()
    activities = SandboxControlActivities(
        ActivityDependencies(
            sessionmaker(session.get_bind(), expire_on_commit=False), Settings(environment="test")
        ),
        registry,
    )
    reference = SandboxReferenceSnapshot(
        uuid4(), runtime.id, "docker", str(uuid4()), "running", None, None
    )
    sandbox = AsyncMock()
    sandbox.create.return_value = reference
    sandbox.resume.return_value = native
    activities._sandbox = sandbox
    native.exec.return_value = ExecResult(
        stdout=b"python", stderr=b"", exit_code=1 if outcome == "nonzero" else 0
    )
    if outcome == "timeout":
        native.exec.side_effect = TimeoutError("private-canary")
    if outcome == "cleanup_failure":
        sandbox.delete.side_effect = OSError("private-canary")
    request = ExecutionRequest(
        execution_id=command.id,
        workspace_id=workspace.id,
        resource_id=command.id,
        kind=ExecutionKind.SANDBOX_CONTROL,
        requested_by_user_id=user.id,
        parameters={"action": "execute"},
    )

    async def exercise():
        environment = ActivityEnvironment()
        if outcome == "cancelled":
            entered = asyncio.Event()

            async def execute(*args, **kwargs):
                entered.set()
                await asyncio.Event().wait()

            native.exec.side_effect = execute
            pending = asyncio.create_task(environment.run(activities.control, request))
            await entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        elif outcome in {"timeout", "cleanup_failure"}:
            with pytest.raises((TimeoutError, OSError)):
                await environment.run(activities.control, request)
        else:
            await environment.run(activities.control, request)

    asyncio.run(exercise())
    session.refresh(command)
    assert command.status == (
        "cancelled" if outcome == "cancelled" else "succeeded" if outcome == "success" else "failed"
    )
    assert command.completed_at and "private-canary" not in str(command.error)
    sandbox.delete.assert_awaited_once_with(workspace.id, reference.id)
    assert session.scalar(select(RuntimeCommand).where(RuntimeCommand.status == "running")) is None
