"""Frozen MCP grants, tenant denial, durable admission and SDK protocol behavior."""

import asyncio
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from agents.mcp import MCPServerStdio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.capabilities.catalog.effective import effective_catalog_fingerprint
from opsmesh.capabilities.mcp.authorized import AuthorizedMCPServer
from opsmesh.capabilities.mcp.execution.contracts import (
    MCPExecutionRequest,
    MCPToolCallDiagnosticResponse,
    MCPToolCallLogResponse,
)
from opsmesh.capabilities.mcp.execution.diagnostics import DIAGNOSTIC_META_KEY
from opsmesh.capabilities.mcp.execution.notifications import MCPNotificationRecorder
from opsmesh.capabilities.mcp.execution.retention import expire_mcp_diagnostics
from opsmesh.capabilities.mcp.execution.validation import MCPExecutionValidator
from opsmesh.capabilities.mcp.models import (
    MCPDeployment,
    MCPServer,
    MCPToolAllowlist,
    MCPToolCallLog,
)
from opsmesh.capabilities.mcp.temporal_servers import _create_server
from opsmesh.capabilities.tools.contracts import ToolPermissionError, ToolResourceNotFoundError
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.authorization.models import ResourceGrant, SecuredResource
from opsmesh.identity.users.models import User
from opsmesh.main import create_app
from opsmesh.orchestration.runs.contracts import RunEventResponse
from opsmesh.orchestration.runs.models import RunEvent, authorization_snapshot_fingerprint
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings, get_settings
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.errors import PolicyDeniedError
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.agent_product import _seed_workspace, _session
from tests.fixtures.mcp_product import seed_mcp_run
from tests.fixtures.native_sandbox import native_boundary


def fixture():
    session = _session()
    _, workspace = _seed_workspace(session, with_default_provider=False)
    run, server = seed_mcp_run(session, workspace)
    return session, workspace, run, server


def request(workspace, run, server):
    return MCPExecutionRequest(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        arguments={"prompt": "private-canary"},
        tool_call_id=str(uuid4()),
    )


def test_mcp_connection_denial_commits_evidence_before_protocol_creation():
    session, workspace, run, server = fixture()
    server.status = "disabled"
    session.commit()
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), Settings(environment="test")
    )
    _, _, backends = native_boundary()
    with pytest.raises(ToolPermissionError):
        _create_server(
            services.transaction, services.settings, backends, request(workspace, run, server)
        )
    assert session.scalars(select(MCPToolCallLog)).one().status == "blocked"
    assert session.scalars(select(SecurityEvent)).one().outcome == "blocked"


def test_managed_mcp_invocation_rejects_readiness_from_an_older_server_version():
    session, workspace, run, server = fixture()
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Native",
        status="active",
        sandbox_backend_id="docker",
        sandbox_options={"image": "python:3.13"},
    )
    session.add(runtime)
    session.flush()
    server.connection = {**server.connection, "runtime": "managed"}
    session.add(
        MCPDeployment(
            workspace_id=workspace.id,
            mcp_server_id=server.id,
            runtime_id=runtime.id,
            status="ready",
            server_version=server.configuration_version - 1,
        )
    )
    session.commit()
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), Settings(environment="test")
    )
    _, client, backends = native_boundary()
    with pytest.raises(PolicyDeniedError, match="readiness is stale"):
        _create_server(
            services.transaction, services.settings, backends, request(workspace, run, server)
        )
    client.create.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        "disabled_server",
        "disabled_tool",
        "stale_server",
        "stale_tool",
        "unhealthy",
        "stale_health",
        "snapshot_tool",
        "snapshot_fingerprint",
    ],
)
def test_current_and_frozen_tool_rejections_are_durable_and_redacted(change):
    session, workspace, run, server = fixture()
    allow = session.scalar(
        select(MCPToolAllowlist).where(MCPToolAllowlist.mcp_server_id == server.id)
    )
    if change == "disabled_server":
        server.status = "disabled"
    elif change == "disabled_tool":
        allow.status = "disabled"
    elif change == "stale_server":
        server.configuration_version += 1
    elif change == "stale_tool":
        allow.configuration_version += 1
    elif change == "unhealthy":
        server.health_status = "unhealthy"
    elif change == "stale_health":
        server.last_health_check_at = datetime.now(UTC) - timedelta(days=10)
    else:
        snapshot = deepcopy(run.input["authorization_snapshot"])
        if change == "snapshot_tool":
            snapshot["capability_catalog"]["tools"] = []
            snapshot["capability_catalog"]["fingerprint"] = effective_catalog_fingerprint(
                snapshot["capability_catalog"]
            )
            snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        else:
            snapshot["fingerprint"] = "invalid"
        run.input = {"authorization_snapshot": snapshot}
    session.commit()
    with pytest.raises(ToolPermissionError):
        MCPExecutionValidator(session, Settings(environment="test")).validate(
            request(workspace, run, server)
        )
    log = session.scalar(select(MCPToolCallLog))
    assert log is not None and log.status == "blocked"
    assert session.scalar(select(SecurityEvent)) is not None
    assert "private-canary" not in json.dumps({"request": log.request, "error": log.error})


def test_foreign_workspace_run_and_server_cannot_be_used():
    session, workspace, run, server = fixture()
    with pytest.raises(ToolResourceNotFoundError):
        MCPExecutionValidator(session, Settings(environment="test")).validate(
            MCPExecutionRequest(
                workspace_id=uuid4(),
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={},
            )
        )
    foreign = MCPServer(
        workspace_id=workspace.id,
        name="other",
        server_type="stdio",
        connection={"command": "other"},
    )
    session.add(foreign)
    session.commit()
    with pytest.raises(ToolPermissionError):
        MCPExecutionValidator(session, Settings(environment="test")).validate(
            request(workspace, run, foreign)
        )


def authorized_fixture(session, workspace, run, server):
    snapshot = deepcopy(run.input["authorization_snapshot"])
    descriptor = snapshot["capability_catalog"]["tools"][0]["descriptor"]
    descriptor["name"] = "echo"
    allow = session.scalar(
        select(MCPToolAllowlist).where(MCPToolAllowlist.mcp_server_id == server.id)
    )
    allow.tool_name = "echo"
    snapshot["allowed_tools"] = ["echo"]
    snapshot["capability_catalog"]["fingerprint"] = effective_catalog_fingerprint(
        snapshot["capability_catalog"]
    )
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run.input = {"authorization_snapshot": snapshot}
    session.commit()
    services = ActivityDependencies(
        sessionmaker(session.get_bind(), expire_on_commit=False), Settings(environment="test")
    )
    notifications = MCPNotificationRecorder(
        services.transaction,
        workspace_id=workspace.id,
        run_id=run.id,
        server_id=server.id,
    )
    return AuthorizedMCPServer(
        lambda: MCPServerStdio(
            params={
                "command": sys.executable,
                "args": [str(Path("tests/fixtures/mcp_stdio_server.py").resolve())],
            },
            failure_error_function=None,
            message_handler=notifications.notify,
        ),
        sessions=services.transaction,
        settings=services.settings,
        workspace_id=workspace.id,
        run_id=run.id,
        server_id=server.id,
        agent_profile_id=UUID(snapshot["agent_profile_id"]),
        capability_catalog_fingerprint=snapshot["capability_catalog"]["fingerprint"],
    )


def test_official_sdk_result_is_recorded_and_same_call_identity_never_replayed():
    session, workspace, run, server = fixture()
    native = authorized_fixture(session, workspace, run, server)

    async def exercise():
        await native.connect()
        try:
            result = await native.call_tool(
                "echo", {"message": "hello"}, {"opsmesh_tool_call_id": "once"}
            )
            assert result.structured_content == {"message": "hello"}
            with pytest.raises(PolicyDeniedError, match="already admitted"):
                await native.call_tool(
                    "echo", {"message": "hello"}, {"opsmesh_tool_call_id": "once"}
                )
        finally:
            await native.cleanup()

    asyncio.run(exercise())
    session.expire_all()
    logs = session.scalars(select(MCPToolCallLog)).all()
    assert len(logs) == 1 and logs[0].status == "completed"
    assert logs[0].response_sha256 is not None


def test_native_protocol_diagnostic_notification_is_private_scoped_and_retained():
    session, workspace, run, server = fixture()
    native = authorized_fixture(session, workspace, run, server)

    async def exercise():
        await native.connect()
        try:
            result = await native.call_tool(
                "echo",
                {"message": "diagnostic-fixture"},
                {"opsmesh_tool_call_id": "notification-once"},
            )
            assert result.structured_content == {"message": "diagnostic-fixture"}
        finally:
            await native.cleanup()

    asyncio.run(exercise())
    session.expire_all()
    log = session.scalars(select(MCPToolCallLog)).one()
    assert log.status == "completed"
    private = log.response["_meta"][DIAGNOSTIC_META_KEY]["notifications"]
    assert private[0]["http_status"] == 503
    assert private[0]["exception_type"] == "ReadTimeout"
    assert "private-notification-canary" not in json.dumps(log.response)
    visible = MCPToolCallLogResponse.model_validate(log).model_dump(mode="json")
    operator = MCPToolCallDiagnosticResponse.model_validate(log).model_dump(mode="json")
    assert DIAGNOSTIC_META_KEY not in json.dumps(visible)
    assert operator["response"]["_meta"][DIAGNOSTIC_META_KEY]["notifications"] == private
    event = session.scalars(
        select(RunEvent).where(RunEvent.event_type == "mcp.diagnostic.notification")
    ).one()
    assert event.workspace_id == workspace.id and event.agent_run_id == run.id
    assert event.event_metadata["scope"] == "tool_call"
    assert event.event_metadata["mcp_tool_call_log_id"] == str(log.id)
    assert "private-notification-canary" not in json.dumps(event.event_metadata)
    assert "diagnostic" not in RunEventResponse.model_validate(event).model_dump()["event_metadata"]
    reader = User(email="mcp-reader@example.com", display_name="MCP reader")
    session.add(reader)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=reader.id, role="viewer"))
    session.flush()
    session.add(
        SecuredResource(
            workspace_id=workspace.id,
            resource_kind="task",
            resource_id=run.task_id,
            owner_user_id=workspace.owner_user_id,
        )
    )
    session.flush()
    session.add(
        ResourceGrant(
            workspace_id=workspace.id,
            resource_kind="task",
            resource_id=run.task_id,
            user_id=reader.id,
            action="read",
        )
    )
    session.commit()
    settings = Settings(environment="test", internal_api_token="mcp-diagnostic-test")
    app = create_app(settings)
    sessions = sessionmaker(session.get_bind(), expire_on_commit=False)

    def database():
        with sessions() as transaction:
            yield transaction

    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        reader_headers = {
            "Authorization": "Bearer mcp-diagnostic-test",
            "X-User-ID": str(reader.id),
        }
        owner_headers = {**reader_headers, "X-User-ID": str(workspace.owner_user_id)}
        visible_events = client.get(
            f"/api/v1/workspaces/{workspace.id}/runs/{run.id}/events", headers=reader_headers
        )
        assert visible_events.status_code == 200
        assert "diagnostic" not in visible_events.json()["items"][-1]["event_metadata"]
        endpoint = (
            f"/api/v1/workspaces/{workspace.id}/capabilities/"
            f"mcp-diagnostic-notifications/{event.id}"
        )
        assert client.get(endpoint, headers=reader_headers).status_code == 403
        operator_event = client.get(endpoint, headers=owner_headers)
        assert operator_event.status_code == 200
        assert operator_event.json()["diagnostic"]["http_status"] == 503
    log.created_at = datetime.now(UTC) - timedelta(days=8)
    event.created_at = log.created_at
    session.commit()
    assert expire_mcp_diagnostics(session, days=7) == 2
    session.commit()
    assert expire_mcp_diagnostics(session, days=7) == 0
    session.refresh(log)
    assert log.status == "completed"
    assert log.response["structuredContent"] == {"message": "diagnostic-fixture"}
    assert DIAGNOSTIC_META_KEY not in json.dumps(log.response)


@pytest.mark.parametrize(
    "denial", ["inactive_run", "missing_identity", "quota", "approval", "oversized"]
)
def test_native_call_admission_denials_never_dispatch(denial):
    session, workspace, run, server = fixture()
    native = authorized_fixture(session, workspace, run, server)
    if denial == "inactive_run":
        run.status = "cancelled"
    elif denial in {"quota", "approval", "oversized"}:
        snapshot = deepcopy(run.input["authorization_snapshot"])
        descriptor = snapshot["capability_catalog"]["tools"][0]["descriptor"]
        if denial == "approval":
            descriptor["requires_approval"] = True
        elif denial == "oversized":
            descriptor["policy"] = {"max_input_bytes": 1}
        else:
            descriptor["policy"] = {"max_calls_per_run": 1}
            session.add(
                MCPToolCallLog(
                    workspace_id=workspace.id,
                    mcp_server_id=server.id,
                    agent_run_id=run.id,
                    tool_name="echo",
                    status="running",
                    request={},
                    created_at=datetime.now(UTC),
                )
            )
        snapshot["capability_catalog"]["fingerprint"] = effective_catalog_fingerprint(
            snapshot["capability_catalog"]
        )
        snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        run.input = {"authorization_snapshot": snapshot}
        native._catalog_fingerprint = snapshot["capability_catalog"]["fingerprint"]
    session.commit()

    async def exercise():
        with pytest.raises(PolicyDeniedError):
            await native.call_tool(
                "echo",
                {"message": "hello"},
                {} if denial == "missing_identity" else {"opsmesh_tool_call_id": "denied"},
            )

    asyncio.run(exercise())


def test_parallel_calls_cannot_overbook_persisted_mcp_budget():
    session, workspace, run, server = fixture()
    native = authorized_fixture(session, workspace, run, server)
    snapshot = deepcopy(run.input["authorization_snapshot"])
    snapshot["capability_catalog"]["tools"][0]["descriptor"]["policy"] = {"max_calls_per_run": 1}
    snapshot["capability_catalog"]["fingerprint"] = effective_catalog_fingerprint(
        snapshot["capability_catalog"]
    )
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run.input = {"authorization_snapshot": snapshot}
    session.commit()
    native._catalog_fingerprint = snapshot["capability_catalog"]["fingerprint"]

    async def exercise():
        await native.connect()
        try:
            results = await asyncio.gather(
                *[
                    native.call_tool(
                        "echo", {"message": "parallel"}, {"opsmesh_tool_call_id": str(index)}
                    )
                    for index in range(2)
                ],
                return_exceptions=True,
            )
            assert len([item for item in results if isinstance(item, PolicyDeniedError)]) == 1
            assert len([item for item in results if not isinstance(item, BaseException)]) == 1
        finally:
            await native.cleanup()

    asyncio.run(exercise())
    session.expire_all()
    assert len(session.scalars(select(MCPToolCallLog)).all()) == 1
