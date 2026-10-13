"""Command admission and Worker revalidation against the real product database."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from temporalio.exceptions import ApplicationError

from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.authorization.errors import AuthenticationError, PermissionDeniedError
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.runtime.commands.service import RuntimeCommandService
from opsmesh.runtime.instances.models import RuntimeCommand, WorkspaceRuntime
from opsmesh.runtime.sandboxes.activities import SandboxControlActivities
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import NotFoundError, PolicyDeniedError
from tests.fixtures.agent_product import _seed_workspace, _session


def fixture():
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
    session.commit()
    settings = Settings(environment="test")
    activities = SandboxControlActivities(
        ActivityDependencies(sessionmaker(session.get_bind(), expire_on_commit=False), settings),
        build_sandbox_backends(settings),
    )
    return session, user, workspace, runtime, settings, activities


def test_queued_command_uses_persisted_arguments_and_stable_cancellation_identity():
    session, user, workspace, runtime, settings, activities = fixture()
    command = RuntimeCommand(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        requested_by_user_id=user.id,
        command=["python", "--version"],
        status="queued",
    )
    session.add(command)
    session.commit()
    request = ExecutionRequest(
        execution_id=command.id,
        workspace_id=workspace.id,
        resource_id=command.id,
        kind=ExecutionKind.SANDBOX_CONTROL,
        requested_by_user_id=user.id,
        parameters={"action": "execute"},
    )
    prepared = activities._prepare_command(request)
    assert prepared is not None and prepared.command == ("python", "--version")
    session.refresh(command)
    assert command.status == "running"
    assert settings


def test_risky_command_rejection_keeps_only_fingerprint_in_audit():
    session, user, workspace, runtime, settings, _ = fixture()
    from opsmesh.governance.policies.models import PlatformPolicy
    from opsmesh.governance.policies.risky_values import RISKY_EXECUTION_POLICY_KEY

    session.add(
        PlatformPolicy(
            policy_key=RISKY_EXECUTION_POLICY_KEY, value={"allow_runtime_commands": False}
        )
    )
    session.commit()
    raw = ["sh", "-c", "rm -rf /; password=private-canary"]
    command = RuntimeCommandService(session, settings).queue_command(
        workspace_id=workspace.id, runtime=runtime, command=raw, requested_by_user_id=user.id
    )
    assert command.status in {"blocked", "waiting_approval"}
    assert session.scalar(select(TemporalOutboxEntry)) is None
    if command.status == "blocked":
        assert session.scalar(select(SecurityEvent)) is not None
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "runtime.command.blocked")
        )
        assert event is not None and "private-canary" not in str(event.audit_metadata)


@pytest.mark.parametrize("denial", ["foreign_workspace", "different_actor", "disabled_environment"])
def test_worker_rechecks_scope_actor_and_environment(denial):
    session, user, workspace, runtime, _, activities = fixture()
    command = RuntimeCommand(
        workspace_id=workspace.id,
        workspace_runtime_id=runtime.id,
        requested_by_user_id=user.id,
        command=["python", "--version"],
        status="queued",
    )
    session.add(command)
    if denial == "disabled_environment":
        runtime.status = "disabled"
    session.commit()
    request = ExecutionRequest(
        workspace_id=uuid4() if denial == "foreign_workspace" else workspace.id,
        resource_id=command.id,
        kind=ExecutionKind.SANDBOX_CONTROL,
        requested_by_user_id=uuid4() if denial == "different_actor" else user.id,
        parameters={"action": "execute"},
    )
    with pytest.raises(
        (
            ApplicationError,
            PermissionError,
            NotFoundError,
            PolicyDeniedError,
            ResourceAccessDeniedError,
            AuthenticationError,
            PermissionDeniedError,
        )
    ):
        activities._prepare_command(request)
    session.refresh(command)
    assert command.status == "queued"
