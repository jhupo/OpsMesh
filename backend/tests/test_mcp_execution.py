from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.admin.models import PlatformPolicy
from backend.app.admin.policies import RISKY_EXECUTION_POLICY_KEY
from backend.app.approvals.models import Approval
from backend.app.capabilities.execution import (
    McpExecutionRequest,
    McpToolExecutionService,
)
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
)
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tools.errors import ToolPermissionError, ToolResourceNotFoundError
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_mcp_execution_authorizes_and_records_events_without_leaking_request() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace)
    credential = McpCredentialReference(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        name="hosted-secret",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload="encrypted-secret",
        secret_fingerprint="sha256:fingerprint",
        encryption_key_id="test",
        scopes=["images.write"],
    )
    session.add(credential)
    session.commit()
    adapter = RecordingAdapter({"asset_id": "img_123", "status": "created"})

    result = McpToolExecutionService(session, adapter).execute(
        McpExecutionRequest(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            mcp_server_id=server.id,
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
        )
    )

    assert result.status == "completed"
    assert result.response == {"asset_id": "img_123", "status": "created"}
    assert adapter.calls[0]["credential_names"] == ["hosted-secret"]
    assert adapter.calls[0]["credential_secret_payloads"] == ["encrypted-secret"]

    logs = session.scalars(select(McpToolCallLog)).all()
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()
    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.agent_run_id == run.id).order_by(TaskMessage.sequence)
    ).all()

    assert len(logs) == 1
    assert logs[0].status == "completed"
    assert logs[0].request["arguments_sha256"]
    assert logs[0].request["authorization_snapshot_version"] == 1
    assert logs[0].request["snapshot_workspace_id"] == str(workspace.id)
    assert logs[0].request["snapshot_allowed_tools"] == ["generate_image"]
    assert "prompt" not in str(logs[0].request)
    assert "mountain" not in str(logs[0].request)
    assert logs[0].response is not None
    assert logs[0].response["result"] == {"asset_id": "img_123", "status": "created"}
    assert [event.event_type for event in events] == ["tool.called", "tool.completed"]
    assert events[0].event_metadata["request_sha256"]
    assert events[0].event_metadata["authorization_snapshot_version"] == 1
    assert "mountain" not in str(events[0].event_metadata)
    assert [message.message_type for message in messages] == ["tool.completed"]
    assert messages[0].payload["response_sha256"]


def test_mcp_execution_blocks_tool_not_in_run_snapshot_and_records_security_event() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])

    try:
        McpToolExecutionService(session, RecordingAdapter({})).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="delete_image",
                arguments={"id": "img_123"},
            )
        )
    except ToolPermissionError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("Expected MCP tool outside snapshot to be blocked")

    log = session.scalar(select(McpToolCallLog))
    security_event = session.scalar(select(SecurityEvent))
    run_events = session.scalars(select(RunEvent).order_by(RunEvent.sequence)).all()
    task_messages = session.scalars(select(TaskMessage).order_by(TaskMessage.sequence)).all()

    assert log is not None
    assert log.status == "blocked"
    assert log.error == {
        "code": "mcp_tool_not_allowed",
        "message": "MCP tool invocation was blocked by policy",
    }
    assert security_event is not None
    assert security_event.action == "mcp_tool.blocked"
    assert security_event.reason == "mcp_tool_not_allowed"
    assert [event.event_type for event in run_events] == ["tool.blocked"]
    assert [message.message_type for message in task_messages] == ["tool.blocked"]


def test_mcp_execution_blocks_tool_not_in_runtime_context() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])

    try:
        McpToolExecutionService(session, RecordingAdapter({})).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
                runtime_allowed_tools=("search_web",),
            )
        )
    except ToolPermissionError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("Expected runtime context tool policy to be enforced")

    log = session.scalar(select(McpToolCallLog))
    security_event = session.scalar(select(SecurityEvent))

    assert log is not None
    assert log.status == "blocked"
    assert log.error is not None
    assert log.error["code"] == "mcp_tool_not_in_runtime_context"
    assert log.request["authorization_snapshot_version"] == 1
    assert security_event is not None
    assert security_event.reason == "mcp_tool_not_in_runtime_context"


def test_mcp_execution_rejects_cross_workspace_run_context() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])
    adapter = RecordingAdapter({})

    try:
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=other_workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    except ToolResourceNotFoundError as exc:
        assert "Agent run not found" in str(exc)
    else:
        raise AssertionError("Expected foreign workspace run to be hidden")

    assert adapter.calls == []
    assert session.scalar(select(McpToolCallLog)) is None
    assert session.scalar(select(SecurityEvent)) is None


def test_mcp_execution_rejects_foreign_mcp_server_for_same_tool_name() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    run, _ = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])
    _, foreign_server = _seed_run_with_mcp_tool(
        session,
        other_workspace,
        snapshot_tools=["generate_image"],
    )
    adapter = RecordingAdapter({})

    try:
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=foreign_server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    except ToolPermissionError as exc:
        assert "mcp_tool_not_allowed" in str(exc)
    else:
        raise AssertionError("Expected foreign MCP server to be blocked")

    log = session.scalar(select(McpToolCallLog))
    security_event = session.scalar(select(SecurityEvent))

    assert adapter.calls == []
    assert log is not None
    assert log.workspace_id == workspace.id
    assert log.mcp_server_id == foreign_server.id
    assert log.status == "blocked"
    assert log.error is not None
    assert log.error["code"] == "mcp_tool_not_allowed"
    assert security_event is not None
    assert security_event.workspace_id == workspace.id
    assert security_event.reason == "mcp_tool_not_allowed"


def test_mcp_execution_rejects_oversized_payload_and_logs_failure() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(
        session,
        workspace,
        allow_policy={"max_input_bytes": 10},
    )

    result = McpToolExecutionService(session, RecordingAdapter({})).execute(
        McpExecutionRequest(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            mcp_server_id=server.id,
            tool_name="generate_image",
            arguments={"prompt": "this payload is too large"},
        )
    )

    log = session.scalar(select(McpToolCallLog))
    events = session.scalars(select(RunEvent).order_by(RunEvent.sequence)).all()

    assert result.status == "failed"
    assert result.error == {
        "code": "mcp_payload_too_large",
        "message": "MCP payload exceeds configured size limit",
    }
    assert log is not None
    assert log.status == "failed"
    assert log.error is not None
    assert log.error["code"] == "mcp_payload_too_large"
    assert [event.event_type for event in events] == ["tool.called", "tool.failed"]


def test_mcp_execution_normalizes_adapter_errors() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace)

    result = McpToolExecutionService(session, FailingAdapter()).execute(
        McpExecutionRequest(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            mcp_server_id=server.id,
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
        )
    )

    assert result.status == "failed"
    assert result.error == {"code": "mcp_adapter_failed", "message": "RuntimeError"}
    assert "sk-secret" not in str(result.error)


def test_mcp_execution_sends_high_risk_tool_to_approval_by_default() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="high")
    adapter = RecordingAdapter({"deleted": True})

    result = McpToolExecutionService(session, adapter).execute(
        McpExecutionRequest(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            mcp_server_id=server.id,
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
        )
    )

    approval = session.scalar(select(Approval))
    log = session.scalar(select(McpToolCallLog))
    session.refresh(run)

    assert result.status == "waiting_approval"
    assert adapter.calls == []
    assert approval is not None
    assert approval.approval_type == "mcp.tool"
    assert approval.risk_level == "high"
    assert log is not None
    assert log.status == "waiting_approval"
    assert run.status == "waiting_approval"


def test_mcp_execution_sends_explicit_approval_tool_to_approval() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, requires_approval=True)
    adapter = RecordingAdapter({"ok": True})

    result = McpToolExecutionService(session, adapter).execute(
        McpExecutionRequest(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            mcp_server_id=server.id,
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
        )
    )

    approval = session.scalar(select(Approval))
    log = session.scalar(select(McpToolCallLog))

    assert result.status == "waiting_approval"
    assert adapter.calls == []
    assert approval is not None
    assert approval.payload["reason"] == "mcp_tool_requires_approval"
    assert approval.payload["requires_approval"] is True
    assert approval.payload["authorization_snapshot_version"] == 1
    assert log is not None
    assert log.status == "waiting_approval"


def test_mcp_execution_allows_high_risk_tool_when_platform_policy_allows_it() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _seed_risky_policy(session, high_risk_tool_mode="allow")
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="high")
    adapter = RecordingAdapter({"ok": True})

    result = McpToolExecutionService(session, adapter).execute(
        McpExecutionRequest(
            workspace_id=workspace.id,
            agent_run_id=run.id,
            mcp_server_id=server.id,
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
        )
    )

    assert result.status == "completed"
    assert adapter.calls


def test_mcp_execution_blocks_high_risk_tool_when_platform_policy_blocks_it() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _seed_risky_policy(session, high_risk_tool_mode="block")
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="high")

    try:
        McpToolExecutionService(session, RecordingAdapter({})).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    except ToolPermissionError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("Expected high-risk MCP tool to be blocked")

    log = session.scalar(select(McpToolCallLog))
    security_event = session.scalar(select(SecurityEvent))

    assert log is not None
    assert log.status == "blocked"
    assert log.error is not None
    assert log.error["code"] == "mcp_high_risk_tool_globally_disabled"
    assert security_event is not None
    assert security_event.reason == "mcp_high_risk_tool_globally_disabled"


class RecordingAdapter:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "server_id": str(server.id),
                "tool_name": tool_name,
                "arguments": arguments,
                "credential_names": [credential.name for credential in credential_refs],
                "credential_secret_payloads": [
                    credential.encrypted_secret_payload for credential in credential_refs
                ],
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._response


class FailingAdapter:
    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[McpCredentialReference],
        timeout_seconds: int,
    ) -> dict[str, object]:
        raise RuntimeError("sk-secret should not be returned")


def _seed_run_with_mcp_tool(
    session: Session,
    workspace: Workspace,
    *,
    snapshot_tools: list[str] | None = None,
    allow_policy: dict[str, object] | None = None,
    risk_level: str = "medium",
    requires_approval: bool = False,
) -> tuple[AgentRun, McpServer]:
    task = Task(workspace_id=workspace.id, title="Create poster")
    session.add(task)
    session.flush()
    step = TaskStep(workspace_id=workspace.id, task_id=task.id, title="Generate image")
    session.add(step)
    session.flush()
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
    )
    session.add(server)
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        capability_key="image.generate",
        risk_level=risk_level,
        requires_approval=requires_approval,
        policy=allow_policy or {},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        input={
            "authorization_snapshot": {
                "version": 1,
                "workspace_id": str(workspace.id),
                "allowed_tools": snapshot_tools or ["generate_image"],
                "runtime_policy": {
                    "mcp": {
                        "timeout_seconds": 15,
                        "max_input_bytes": 64_000,
                        "max_output_bytes": 256_000,
                    }
                },
            }
        },
    )
    session.add_all([allow, run])
    session.commit()
    return run, server


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "acme",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _seed_risky_policy(
    session: Session,
    *,
    high_risk_tool_mode: str,
) -> PlatformPolicy:
    policy = PlatformPolicy(
        policy_key=RISKY_EXECUTION_POLICY_KEY,
        value={
            "allow_runtime_commands": True,
            "allow_network_egress": False,
            "allow_self_hosted_runtimes": True,
            "require_approval_for_high_risk_tools": (
                high_risk_tool_mode == "require_workspace_approval"
            ),
            "high_risk_tool_mode": high_risk_tool_mode,
        },
        description="test",
    )
    session.add(policy)
    session.commit()
    return policy


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
