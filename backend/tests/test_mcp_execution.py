from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.admin.models import PlatformPolicy
from backend.app.admin.risky_policy_values import RISKY_EXECUTION_POLICY_KEY
from backend.app.approvals.models import Approval
from backend.app.capabilities.effective_catalog import effective_catalog_fingerprint
from backend.app.capabilities.execution import McpToolExecutionService
from backend.app.capabilities.mcp.adapter_resolver import McpAdapterResolver
from backend.app.capabilities.mcp.types import (
    McpExecutionError,
    McpExecutionRequest,
)
from backend.app.capabilities.mcp.remote_adapters import SseMcpToolAdapter
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
)
from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourcePolicyReviewBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tools.errors import ToolPermissionError, ToolResourceNotFoundError
from backend.app.workspaces.models import Workspace, WorkspaceMember


@pytest.fixture(autouse=True)
def _approve_semantic_tool_execution_review(monkeypatch: pytest.MonkeyPatch) -> None:
    def approved_review(self: ResourcePolicyReviewBuilder, **_: object) -> ResourceReview:
        return ResourceReview(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "codex-auto-review"},
        )

    monkeypatch.setattr(
        ResourcePolicyReviewBuilder,
        "review_tool_execution",
        approved_review,
    )


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

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
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
    assert logs[0].task_id == run.task_id
    assert logs[0].task_step_id == run.task_step_id
    assert logs[0].agent_profile_id == run.agent_profile_id
    assert logs[0].latency_ms is not None
    assert logs[0].argument_sha256 == logs[0].request["arguments_sha256"]
    assert logs[0].response_sha256 is not None
    assert logs[0].error_code is None
    assert logs[0].request["arguments_sha256"]
    assert logs[0].request["authorization_snapshot_version"] == 2
    assert logs[0].request["snapshot_workspace_id"] == str(workspace.id)
    assert logs[0].request["snapshot_allowed_tools"] == ["generate_image"]
    assert "prompt" not in str(logs[0].request)
    assert "mountain" not in str(logs[0].request)
    assert logs[0].response is not None
    assert logs[0].response["result"] == {"asset_id": "img_123", "status": "created"}
    assert [event.event_type for event in events] == ["tool.called", "tool.completed"]
    assert events[0].event_metadata["request_sha256"]
    assert events[0].event_metadata["authorization_snapshot_version"] == 2
    assert "mountain" not in str(events[0].event_metadata)
    assert [message.message_type for message in messages] == ["tool.completed"]
    assert messages[0].payload["response_sha256"]


def test_mcp_execution_ignores_disabled_credentials() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace)
    disabled = McpCredentialReference(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        name="disabled-server-secret",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload="disabled-secret",
        status="disabled",
    )
    active_workspace = McpCredentialReference(
        workspace_id=workspace.id,
        mcp_server_id=None,
        name="active-workspace-secret",
        provider="hosted",
        external_ref="",
        encrypted_secret_payload="active-secret",
        status="active",
    )
    session.add_all([disabled, active_workspace])
    session.commit()
    adapter = RecordingAdapter({"ok": True})

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    )

    assert result.status == "completed"
    assert adapter.calls[0]["credential_names"] == ["active-workspace-secret"]
    assert adapter.calls[0]["credential_secret_payloads"] == ["active-secret"]


def test_mcp_execution_enforces_frozen_schema_defaults_and_locked_parameters() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="low")
    snapshot = deepcopy(run.input["authorization_snapshot"])
    catalog = snapshot["capability_catalog"]
    assert isinstance(catalog, dict)
    tools = catalog["tools"]
    assert isinstance(tools, list)
    tool = tools[0]
    assert isinstance(tool, dict)
    descriptor = tool["descriptor"]
    assert isinstance(descriptor, dict)
    descriptor["input_schema"] = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "style": {"type": "string", "enum": ["safe", "creative"]},
        },
        "required": ["prompt", "style"],
        "additionalProperties": False,
    }
    tool["parameters"] = {"style": "safe"}
    tool["locked_parameters"] = ["style"]
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run.input = {"authorization_snapshot": snapshot}
    session.commit()
    adapter = RecordingAdapter({"ok": True})

    with pytest.raises(ToolPermissionError, match="mcp_tool_parameter_locked"):
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain", "style": "creative"},
                )
            )
        )

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    )

    assert result.status == "completed"
    assert adapter.calls[0]["arguments"] == {"prompt": "mountain", "style": "safe"}


def test_mcp_execution_rejects_disabled_server_or_tool_allowlist() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace)
    adapter = RecordingAdapter({"ok": True})
    server.status = "disabled"
    session.commit()

    try:
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={},
                )
            )
        )
    except ToolPermissionError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("Expected disabled MCP server to be blocked")

    server.status = "active"
    allow = session.scalar(
        select(McpToolAllowlist).where(McpToolAllowlist.mcp_server_id == server.id)
    )
    assert allow is not None
    allow.status = "disabled"
    session.commit()

    try:
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={},
                )
            )
        )
    except ToolPermissionError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("Expected disabled MCP tool allowlist to be blocked")

    logs = session.scalars(select(McpToolCallLog).order_by(McpToolCallLog.created_at)).all()
    assert [log.status for log in logs] == ["blocked", "blocked"]
    assert [log.error_code for log in logs] == ["mcp_tool_not_allowed", "mcp_tool_not_allowed"]
    assert adapter.calls == []


def test_mcp_execution_blocks_tool_not_in_run_snapshot_and_records_security_event() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])

    try:
        asyncio.run(
            McpToolExecutionService(session, RecordingAdapter({})).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="delete_image",
                    arguments={"id": "img_123"},
                )
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
    assert log.task_id == run.task_id
    assert log.task_step_id == run.task_step_id
    assert log.agent_profile_id == run.agent_profile_id
    assert log.latency_ms == 0
    assert log.argument_sha256 == log.request["arguments_sha256"]
    assert log.error_code == "mcp_tool_not_in_run_snapshot"
    assert log.error == {
        "code": "mcp_tool_not_in_run_snapshot",
        "message": "MCP tool invocation was blocked by policy",
    }
    assert security_event is not None
    assert security_event.action == "mcp_tool.blocked"
    assert security_event.reason == "mcp_tool_not_in_run_snapshot"
    assert [event.event_type for event in run_events] == ["tool.blocked"]
    assert [message.message_type for message in task_messages] == ["tool.blocked"]


def test_mcp_execution_blocks_tool_not_in_runtime_context() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])

    try:
        asyncio.run(
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
    assert log.request["authorization_snapshot_version"] == 2
    assert security_event is not None
    assert security_event.reason == "mcp_tool_not_in_runtime_context"


def test_mcp_execution_blocks_unhealthy_server_before_adapter_call() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])
    server.health_status = "unhealthy"
    server.last_error = "health_check_failed"
    session.commit()
    adapter = RecordingAdapter({"ok": True})

    try:
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain"},
                )
            )
        )
    except ToolPermissionError as exc:
        assert "mcp_server_unhealthy" in str(exc)
    else:
        raise AssertionError("Expected unhealthy MCP server to be blocked")

    log = session.scalar(select(McpToolCallLog))
    security_event = session.scalar(select(SecurityEvent))

    assert adapter.calls == []
    assert log is not None
    assert log.status == "blocked"
    assert log.error is not None
    assert log.error["code"] == "mcp_server_unhealthy"
    assert log.request["authorization_snapshot_version"] == 2
    assert security_event is not None
    assert security_event.reason == "mcp_server_unhealthy"


def test_mcp_execution_blocks_stale_health_check_before_adapter_call() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])
    server.health_status = "healthy"
    server.last_health_check_at = datetime.now(UTC) - timedelta(hours=25)
    session.commit()
    adapter = RecordingAdapter({"ok": True})

    try:
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain"},
                )
            )
        )
    except ToolPermissionError as exc:
        assert "mcp_server_health_check_stale" in str(exc)
    else:
        raise AssertionError("Expected stale MCP health check to be blocked")

    log = session.scalar(select(McpToolCallLog))
    security_event = session.scalar(select(SecurityEvent))

    assert adapter.calls == []
    assert log is not None
    assert log.status == "blocked"
    assert log.error is not None
    assert log.error["code"] == "mcp_server_health_check_stale"
    assert log.request["authorization_snapshot_version"] == 2
    assert security_event is not None
    assert security_event.reason == "mcp_server_health_check_stale"


def test_mcp_execution_uses_configured_health_check_stale_window() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])
    server.health_status = "healthy"
    server.last_health_check_at = datetime.now(UTC) - timedelta(hours=25)
    session.commit()
    adapter = RecordingAdapter({"ok": True})

    result = asyncio.run(
        McpToolExecutionService(
            session,
            adapter,
            settings=Settings(mcp_health_check_stale_after_seconds=26 * 60 * 60),
        ).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    )

    log = session.scalar(select(McpToolCallLog))

    assert result.status == "completed"
    assert adapter.calls
    assert log is not None
    assert log.status == "completed"


def test_mcp_execution_rejects_cross_workspace_run_context() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    run, server = _seed_run_with_mcp_tool(session, workspace, snapshot_tools=["generate_image"])
    adapter = RecordingAdapter({})

    try:
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=other_workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain"},
                )
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
        asyncio.run(
            McpToolExecutionService(session, adapter).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=foreign_server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain"},
                )
            )
        )
    except ToolPermissionError as exc:
        assert "mcp_tool_not_in_run_snapshot" in str(exc)
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
    assert log.error["code"] == "mcp_tool_not_in_run_snapshot"
    assert security_event is not None
    assert security_event.workspace_id == workspace.id
    assert security_event.reason == "mcp_tool_not_in_run_snapshot"


def test_mcp_execution_rejects_oversized_payload_and_logs_failure() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(
        session,
        workspace,
        allow_policy={"max_input_bytes": 10},
    )

    result = asyncio.run(
        McpToolExecutionService(session, RecordingAdapter({})).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "this payload is too large"},
            )
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


def test_mcp_execution_raises_unknown_adapter_errors_after_logging() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace)

    with pytest.raises(RuntimeError):
        asyncio.run(
            McpToolExecutionService(session, FailingAdapter()).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain"},
                )
            )
        )

    log = session.scalar(select(McpToolCallLog))
    event = session.scalar(
        select(RunEvent)
        .where(RunEvent.event_type == "tool.failed")
        .order_by(RunEvent.sequence.desc())
    )

    assert log is not None
    assert log.status == "failed"
    assert log.error is not None
    assert log.error["code"] == "mcp_adapter_crashed"
    assert log.error["message"] == "RuntimeError"
    assert event is not None
    assert event.event_metadata["error"] == {
        "code": "mcp_adapter_crashed",
        "message": "RuntimeError",
    }
    assert "sk-secret" not in str(log.error)
    assert "sk-secret" not in str(event.event_metadata)


def test_mcp_execution_sends_high_risk_tool_to_approval_by_default() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="high")
    adapter = RecordingAdapter({"deleted": True})

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
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

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    )

    approval = session.scalar(select(Approval))
    log = session.scalar(select(McpToolCallLog))

    assert result.status == "waiting_approval"
    assert adapter.calls == []
    assert approval is not None
    assert approval.payload["reason"] == "mcp_tool_requires_approval"
    assert approval.payload["requires_approval"] is True
    assert approval.payload["authorization_snapshot_version"] == 2
    assert log is not None
    assert log.status == "waiting_approval"
    assert log.approval_id == approval.id


def test_mcp_execution_review_sends_sensitive_arguments_to_admin_approval() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="low")
    adapter = RecordingAdapter({"ok": True})

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={
                    "prompt": "write production token to artifact",
                    "api_key": "sk-secret",
                },
            )
        )
    )

    approval = session.scalar(select(Approval))
    log = session.scalar(select(McpToolCallLog))

    assert result.status == "waiting_approval"
    assert adapter.calls == []
    assert approval is not None
    assert approval.approval_type == "mcp.tool"
    assert approval.risk_level == "high"
    assert approval.payload["reason"] == "mcp_tool_execution_review_requires_approval"
    assert approval.payload["arguments_preview"]["api_key"] == "[redacted]"
    assert approval.payload["execution_review"]["risk_level"] == "high"
    assert (
        "tool.arguments.contains_sensitive_keys" in approval.payload["execution_review"]["reasons"]
    )
    assert log is not None
    assert log.status == "waiting_approval"
    assert log.approval_id == approval.id
    assert "sk-secret" not in str(approval.payload)


def test_mcp_execution_review_still_requires_approval_for_high_risk_tool() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _seed_risky_policy(session, high_risk_tool_mode="allow")
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="high")
    adapter = RecordingAdapter({"ok": True})

    result = asyncio.run(
        McpToolExecutionService(session, adapter).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    )

    approval = session.scalar(select(Approval))

    assert result.status == "waiting_approval"
    assert adapter.calls == []
    assert approval is not None
    assert approval.payload["reason"] == "mcp_tool_execution_review_requires_approval"


def test_mcp_execution_blocks_high_risk_tool_when_platform_policy_blocks_it() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _seed_risky_policy(session, high_risk_tool_mode="block")
    run, server = _seed_run_with_mcp_tool(session, workspace, risk_level="high")

    try:
        asyncio.run(
            McpToolExecutionService(session, RecordingAdapter({})).execute(
                McpExecutionRequest(
                    workspace_id=workspace.id,
                    agent_run_id=run.id,
                    mcp_server_id=server.id,
                    tool_name="generate_image",
                    arguments={"prompt": "mountain"},
                )
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


def test_mcp_execution_uses_sse_adapter_with_credential_headers(monkeypatch) -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    run, server = _seed_run_with_mcp_tool(session, workspace)
    server.server_type = "sse"
    server.connection = {
        "url": "https://mcp.example.test/sse",
        "headers": {"x-client": "opsmesh"},
    }
    credential = McpCredentialReference(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        name="api-key",
        provider="static_header",
        external_ref="x-api-key: test-secret",
        scopes=["images.write"],
    )
    session.add(credential)
    session.commit()
    sdk = _FakeSseSdk(_FakeSdkCallToolResult(structured_content={"status": "created"}))
    monkeypatch.setattr(
        "backend.app.capabilities.mcp.remote_adapters.sse_client",
        sdk.sse_client,
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp.remote_adapters.ClientSession",
        sdk.client_session,
    )

    result = asyncio.run(
        McpToolExecutionService(session, McpAdapterResolver()).execute(
            McpExecutionRequest(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
            )
        )
    )

    assert result.status == "completed"
    assert result.response == {"status": "created"}
    assert sdk.transport_call == {
        "url": "https://mcp.example.test/sse",
        "timeout": 15,
        "sse_read_timeout": 15,
        "headers": {"x-client": "opsmesh", "x-api-key": "test-secret"},
    }
    assert sdk.initialize_calls == 1
    assert sdk.tool_call == {
        "name": "generate_image",
        "arguments": {"prompt": "mountain"},
        "timeout_seconds": 15,
    }


def test_mcp_sse_adapter_normalizes_remote_errors(monkeypatch) -> None:
    server = McpServer(
        name="image-tools",
        server_type="sse",
        connection={"url": "https://mcp.example.test/sse"},
    )

    sdk = _FakeSseSdk(_FakeSdkCallToolResult(is_error=True))
    monkeypatch.setattr(
        "backend.app.capabilities.mcp.remote_adapters.sse_client",
        sdk.sse_client,
    )
    monkeypatch.setattr(
        "backend.app.capabilities.mcp.remote_adapters.ClientSession",
        sdk.client_session,
    )

    try:
        asyncio.run(
            SseMcpToolAdapter().call(
                server=server,
                tool_name="generate_image",
                arguments={"prompt": "mountain"},
                credential_refs=[],
                timeout_seconds=15,
            )
        )
    except McpExecutionError as exc:
        assert exc.code == "mcp_remote_error"
        assert "sk-secret" not in str(exc)
    else:
        raise AssertionError("Expected SSE remote errors to be normalized")


class _FakeSdkCallToolResult:
    def __init__(
        self,
        *,
        structured_content: dict[str, object] | None = None,
        is_error: bool = False,
    ) -> None:
        self.content: list[object] = []
        self.structuredContent = structured_content
        self.isError = is_error


class _FakeSseTransport:
    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSseClientSession:
    def __init__(self, sdk: _FakeSseSdk) -> None:
        self._sdk = sdk

    async def __aenter__(self) -> _FakeSseClientSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def initialize(self) -> None:
        self._sdk.initialize_calls += 1

    async def call_tool(
        self,
        name: str,
        *,
        arguments: dict[str, object],
        read_timeout_seconds: timedelta,
    ) -> _FakeSdkCallToolResult:
        self._sdk.tool_call = {
            "name": name,
            "arguments": arguments,
            "timeout_seconds": read_timeout_seconds.total_seconds(),
        }
        return self._sdk.result


class _FakeSseSdk:
    def __init__(self, result: _FakeSdkCallToolResult) -> None:
        self.result = result
        self.transport_call: dict[str, object] = {}
        self.initialize_calls = 0
        self.tool_call: dict[str, object] = {}

    def sse_client(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: int,
        sse_read_timeout: int,
    ) -> _FakeSseTransport:
        self.transport_call = {
            "url": url,
            "headers": headers,
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }
        return _FakeSseTransport()

    def client_session(self, read_stream: object, write_stream: object) -> _FakeSseClientSession:
        return _FakeSseClientSession(self)


class RecordingAdapter:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def call(
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
    async def call(
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
    session.add(allow)
    session.flush()
    requested_tools = snapshot_tools or ["generate_image"]
    catalog: dict[str, object] = {
        "catalog_version": 1,
        "workspace_id": str(workspace.id),
        "agent_profile_id": str(uuid4()),
        "agent_profile_version": 1,
        "team_id": None,
        "team_policy_version": None,
        "team_member_id": None,
        "department": None,
        "tools": [],
        "resources": [],
        "denied": [],
    }
    if "generate_image" in requested_tools:
        catalog["tools"] = [
            {
                "descriptor": {
                    "name": "generate_image",
                    "source": "mcp",
                    "description": allow.description,
                    "input_schema": allow.input_schema,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "capability_key": allow.capability_key,
                    "mcp_server_id": str(server.id),
                    "mcp_tool_allowlist_id": str(allow.id),
                    "mcp_server_name": server.name,
                    "policy": allow.policy,
                    "required_resource_type": None,
                    "required_access_modes": [],
                },
                "parameters": {},
                "locked_parameters": [],
                "provenance": [],
            }
        ]
    catalog["fingerprint"] = effective_catalog_fingerprint(catalog)
    snapshot: dict[str, object] = {
        "version": 2,
        "workspace_id": str(workspace.id),
        "allowed_tools": requested_tools,
        "capability_catalog": catalog,
        "runtime_policy": {
            "mcp": {
                "timeout_seconds": 15,
                "max_input_bytes": 64_000,
                "max_output_bytes": 256_000,
            }
        },
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        status=RunStatus.RUNNING.value,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
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
