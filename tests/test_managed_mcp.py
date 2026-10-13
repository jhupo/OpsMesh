"""Managed MCP intents and generation handling use durable native execution refs."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from temporalio.testing import ActivityEnvironment

from opsmesh.capabilities.mcp.activities import ManagedMCPActivities
from opsmesh.capabilities.mcp.managed_schemas import ManagedMCPCreateRequest, StdioProjectConfig
from opsmesh.capabilities.mcp.managed_service import ManagedMCPService
from opsmesh.capabilities.mcp.models import MCPCredentialReference, MCPServer
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.shared.errors import ConflictError, NotFoundError
from tests.fixtures.agent_product import (  # noqa: F401
    _seed_workspace,
    _session,
)


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
    service = ManagedMCPService(session, settings)
    return session, user, workspace, runtime, settings, service


def create(f):
    _, user, workspace, runtime, _, service = f
    return service.create(
        workspace.id,
        AuthenticatedUser.from_model(user),
        ManagedMCPCreateRequest(
            runtime_id=runtime.id,
            mcpServers={
                "echo": StdioProjectConfig(
                    command="python",
                    args=["/app/server.py"],
                    env={"BUSINESS_KEY": "private-canary"},
                )
            },
        ),
    )[0]


def test_import_encrypts_credentials_and_stages_only_resource_references():
    f = fixture()
    session, _, workspace, runtime, settings, _ = f
    deployment = create(f)
    server = session.get(MCPServer, deployment.mcp_server_id)
    credential = session.scalar(select(MCPCredentialReference))
    dispatch = session.scalars(select(TemporalOutboxEntry)).one()
    assert deployment.status == "queued" and deployment.runtime_id == runtime.id
    assert (
        credential.encrypted_secret_payload
        and "private-canary" not in credential.encrypted_secret_payload
    )
    evidence = json.dumps({"connection": server.connection, "dispatch": dispatch.payload})
    assert "private-canary" not in evidence and "BUSINESS_KEY" not in evidence
    request = ExecutionRequest.model_validate(dispatch.payload["execution"])
    assert request.workspace_id == workspace.id and request.resource_id == deployment.id
    assert request.kind == ExecutionKind.MCP_CONTROL
    assert request.parameters == {"action": "start", "deployment_version": 1}
    assert settings.credential_encryption_secret


def test_duplicate_control_stages_one_intent_and_old_generation_is_ignored():
    f = fixture()
    session, user, workspace, _, settings, service = f
    deployment = create(f)
    service.control(
        workspace.id, deployment.mcp_server_id, AuthenticatedUser.from_model(user), "start"
    )
    assert len(session.scalars(select(TemporalOutboxEntry)).all()) == 1
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), settings
    )
    activities = ManagedMCPActivities(services, build_sandbox_backends(settings))
    old = ExecutionRequest(
        workspace_id=workspace.id,
        resource_id=deployment.id,
        kind=ExecutionKind.MCP_CONTROL,
        requested_by_user_id=user.id,
        parameters={"action": "start", "deployment_version": 1},
    )
    service.control(
        workspace.id, deployment.mcp_server_id, AuthenticatedUser.from_model(user), "stop"
    )
    assert activities._prepare(old, "start", 1) is None
    current = ExecutionRequest(
        workspace_id=workspace.id,
        resource_id=deployment.id,
        kind=ExecutionKind.MCP_CONTROL,
        requested_by_user_id=user.id,
        parameters={"action": "stop", "deployment_version": 2},
    )
    asyncio.run(ActivityEnvironment().run(activities.control, current))
    session.refresh(deployment)
    assert deployment.status == "stopped" and deployment.generation == 2


def test_binding_requires_stopped_deployment_and_tenant_environment():
    f = fixture()
    session, user, workspace, runtime, _, service = f
    deployment = create(f)
    with pytest.raises(ConflictError):
        service.bind_environment(
            workspace.id, deployment.mcp_server_id, AuthenticatedUser.from_model(user), runtime.id
        )
    deployment.status = "stopped"
    session.commit()
    with pytest.raises(NotFoundError):
        service.bind_environment(
            workspace.id, deployment.mcp_server_id, AuthenticatedUser.from_model(user), uuid4()
        )
    session.rollback()
    with pytest.raises(NotFoundError):
        service.get(uuid4(), deployment.mcp_server_id)


def test_worker_rechecks_environment_and_actor_before_credentials_or_sdk():
    f = fixture()
    session, user, workspace, runtime, settings, _ = f
    deployment = create(f)
    runtime.status = "disabled"
    session.commit()
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), settings
    )
    activities = ManagedMCPActivities(services, build_sandbox_backends(settings))
    request = ExecutionRequest(
        workspace_id=workspace.id,
        resource_id=deployment.id,
        kind=ExecutionKind.MCP_CONTROL,
        requested_by_user_id=user.id,
        parameters={"action": "start", "deployment_version": 1},
    )
    with pytest.raises(Exception, match="environment"):
        asyncio.run(ActivityEnvironment().run(activities.control, request))
    session.refresh(deployment)
    assert deployment.status == "failed"
