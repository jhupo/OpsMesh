from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_runtime.contracts import AgentRuntimeContext
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.models import McpServer, McpToolAllowlist, McpToolCallLog
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.service import ResourcePolicyReviewBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import RuntimeCommandResult
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.models import SelfHostedMcpJob
from backend.app.tasks.models import Task
from backend.app.tools.errors import ToolPermissionError
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


def test_backend_tool_executor_routes_allowed_tool_to_mcp_execution() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    server = McpServer(workspace_id=workspace.id, name="image-tools")
    session.add_all([task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.commit()

    result = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=("generate_image",),
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
    )

    assert result.status == "completed"
    assert result.output == {"ok": True, "tool": "generate_image"}
    assert result.metadata["provenance"] == "backend_tool_executor"
    assert result.metadata["tool_kind"] == "mcp"
    assert result.metadata["tool_name"] == "generate_image"
    assert result.metadata["workspace_id"] == str(workspace.id)
    assert result.metadata["run_id"] == str(run.id)
    assert isinstance(result.metadata["mcp_tool_call_log_id"], str)


def test_backend_tool_executor_records_team_runtime_tool_provenance() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Team task")
    server = McpServer(workspace_id=workspace.id, name="team-tools")
    session.add_all([task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.commit()
    team_id = uuid4()
    member_id = uuid4()
    runtime_id = uuid4()
    runtime_space_id = uuid4()
    thread_id = uuid4()
    team_session_id = uuid4()
    agent_profile_id = uuid4()

    result = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=("generate_image",),
            metadata={
                "team_context": {
                    "team_id": str(team_id),
                    "team_name": "Product Team",
                    "team_type": "software",
                    "current_member": {
                        "member_id": str(member_id),
                        "agent_profile_id": str(agent_profile_id),
                        "team_role": "Builder",
                        "department": "Engineering",
                        "position_title": "Backend Engineer",
                        "responsibilities": ["ignored in provenance"],
                    },
                    "runtime": {
                        "status": "running",
                        "workspace_runtime_id": str(runtime_id),
                        "runtime_status": "running",
                        "runtime_space_id": str(runtime_space_id),
                        "thread_id": str(thread_id),
                        "team_session_id": str(team_session_id),
                        "member_session_count": 3,
                    },
                }
            },
        ),
        tool_name="generate_image",
        arguments={"prompt": "runtime provenance"},
    )

    assert result.status == "completed"
    assert result.metadata["team"] == {
        "team_id": str(team_id),
        "team_name": "Product Team",
        "team_type": "software",
        "current_member": {
            "member_id": str(member_id),
            "agent_profile_id": str(agent_profile_id),
            "team_role": "Builder",
            "department": "Engineering",
            "position_title": "Backend Engineer",
        },
        "runtime": {
            "status": "running",
            "workspace_runtime_id": str(runtime_id),
            "runtime_status": "running",
            "runtime_space_id": str(runtime_space_id),
            "thread_id": str(thread_id),
            "team_session_id": str(team_session_id),
            "member_session_count": 3,
        },
    }
    assert "responsibilities" not in result.metadata["team"]["current_member"]


def test_backend_tool_executor_enforces_mcp_per_run_call_limit() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    server = McpServer(workspace_id=workspace.id, name="image-tools")
    session.add_all([task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        policy={"max_calls_per_run": 1},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.commit()
    executor = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter())
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=("generate_image",),
    )

    first = executor.execute_tool(
        context=context,
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
    )
    try:
        executor.execute_tool(
            context=context,
            tool_name="generate_image",
            arguments={"prompt": "forest"},
        )
    except ToolPermissionError as exc:
        assert "mcp_tool_run_call_limit_exceeded" in str(exc)
    else:
        raise AssertionError("Expected MCP per-run call limit to block repeated calls")

    logs = session.query(McpToolCallLog).order_by(McpToolCallLog.created_at.asc()).all()
    assert first.status == "completed"
    assert [log.status for log in logs] == ["completed", "blocked"]
    assert logs[1].mcp_server_id == server.id
    assert logs[1].error_code == "mcp_tool_run_call_limit_exceeded"


def test_backend_tool_executor_enforces_mcp_hourly_call_limit_across_runs() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    server = McpServer(workspace_id=workspace.id, name="image-tools")
    session.add_all([task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        policy={"max_calls_per_hour": 1},
    )
    first_run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    second_run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, first_run, second_run])
    session.commit()
    executor = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter())

    first = executor.execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=first_run.id,
            allowed_tools=("generate_image",),
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
    )
    try:
        executor.execute_tool(
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=task.id,
                run_id=second_run.id,
                allowed_tools=("generate_image",),
            ),
            tool_name="generate_image",
            arguments={"prompt": "forest"},
        )
    except ToolPermissionError as exc:
        assert "mcp_tool_hourly_call_limit_exceeded" in str(exc)
    else:
        raise AssertionError("Expected MCP hourly call limit to apply across runs")

    logs = session.query(McpToolCallLog).order_by(McpToolCallLog.created_at.asc()).all()
    assert first.status == "completed"
    assert [log.status for log in logs] == ["completed", "blocked"]
    assert logs[1].agent_run_id == second_run.id
    assert logs[1].error_code == "mcp_tool_hourly_call_limit_exceeded"


def test_backend_tool_executor_enforces_runtime_allowed_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    server = McpServer(workspace_id=workspace.id, name="image-tools")
    session.add_all([task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.commit()

    try:
        BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=task.id,
                run_id=run.id,
                allowed_tools=(),
            ),
            tool_name="generate_image",
            arguments={"prompt": "mountain"},
        )
    except ToolPermissionError as exc:
        assert "mcp_tool_not_in_runtime_context" in str(exc)
    else:
        raise AssertionError("Expected backend tool executor to enforce runtime context")


def test_backend_tool_executor_dispatches_agent_mailbox_product_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    _, other_workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    other_agent = AgentProfile(
        workspace_id=other_workspace.id,
        name="Foreign",
        role="builder",
    )
    session.add_all([task, sender, recipient, other_agent])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=sender.id,
        status=RunStatus.RUNNING.value,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["send_agent_message", "list_agent_thread_messages"],
            }
        },
    )
    session.add(run)
    session.commit()
    executor = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter())
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=("send_agent_message", "list_agent_thread_messages"),
    )

    sent = executor.execute_tool(
        context=context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(recipient.id),
            "subject": "Handoff",
            "body": "Please continue.",
            "payload": {"scope": "backend"},
        },
    )
    assert sent.status == "completed"
    assert sent.output is not None
    assert sent.metadata["provenance"] == "backend_tool_executor"
    assert sent.metadata["tool_kind"] == "product"
    assert sent.metadata["tool_name"] == "send_agent_message"
    listed = executor.execute_tool(
        context=context,
        tool_name="list_agent_thread_messages",
        arguments={"thread_id": sent.output["thread"]["id"]},
    )
    sensitive = executor.execute_tool(
        context=context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(recipient.id),
            "body": "Please review sensitive context.",
            "payload": {"token": "hidden", "scope": "backend"},
        },
    )
    blocked = executor.execute_tool(
        context=context,
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(other_agent.id),
            "body": "Cross workspace should fail.",
        },
    )

    stored = session.query(AgentMessage).one()
    assert sent.output["message"]["payload"] == {"scope": "backend"}
    assert listed.status == "completed"
    assert listed.output is not None
    assert listed.output["total"] == 1
    assert stored.payload == {"scope": "backend"}
    assert sensitive.status == "waiting_approval"
    assert sensitive.metadata["review_risk_level"] == "high"
    assert blocked.status == "failed"
    assert blocked.error is not None
    assert blocked.error["code"] == "product_tool_failed"


def test_backend_tool_executor_redacts_product_tool_failure_messages(
    monkeypatch,
) -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    task = Task(workspace_id=workspace.id, title="Task")
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["send_agent_message"],
            }
        },
    )
    session.add_all([recipient, task, run])
    session.commit()

    def fail_send_agent_message(*args, **kwargs):
        raise ValueError("provider rejected api_key=sk-product-tool-secret")

    monkeypatch.setattr(
        "backend.app.agent_runtime.product_tool_executor.ProductToolService.send_agent_message",
        fail_send_agent_message,
    )

    result = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=("send_agent_message",),
        ),
        tool_name="send_agent_message",
        arguments={
            "recipient_agent_profile_id": str(recipient.id),
            "body": "Should fail before sending.",
        },
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "product_tool_failed"
    assert "sk-product-tool-secret" not in result.error["message"]
    assert "[redacted]" in result.error["message"]
    assert result.metadata["provenance"] == "backend_tool_executor"
    assert result.metadata["tool_kind"] == "product"


def test_backend_tool_executor_dispatches_agent_inbox_product_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([task, sender, recipient])
    session.flush()
    thread = AgentMessageThread(workspace_id=workspace.id, task_id=task.id, subject="Inbox")
    session.add(thread)
    session.flush()
    message = AgentMessage(
        workspace_id=workspace.id,
        task_id=task.id,
        thread_id=thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Please pick this up.",
        payload={"token": "hidden-inbox-token"},
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=recipient.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["get_agent_inbox", "mark_agent_message_read"],
            }
        },
    )
    session.add_all([message, run])
    session.commit()
    executor = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter())
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=("get_agent_inbox", "mark_agent_message_read"),
    )

    inbox = executor.execute_tool(
        context=context,
        tool_name="get_agent_inbox",
        arguments={"latest_limit": 5, "unread_only": True},
    )
    marked = executor.execute_tool(
        context=context,
        tool_name="mark_agent_message_read",
        arguments={"message_id": str(message.id)},
    )

    session.refresh(message)
    assert inbox.status == "completed"
    assert inbox.output is not None
    assert inbox.output["unread_count"] == 1
    assert inbox.output["latest_messages"][0]["payload"] == {"token": "[redacted]"}
    assert marked.status == "completed"
    assert marked.output is not None
    assert marked.output["message"]["status"] == "read"
    assert message.status == "read"
    assert message.read_at is not None


def test_backend_tool_executor_scopes_agent_inbox_to_runtime_metadata() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    current_task = Task(workspace_id=workspace.id, title="Current task")
    other_task = Task(workspace_id=workspace.id, title="Other task")
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([current_task, other_task, sender, recipient])
    session.flush()
    current_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=current_task.id,
        subject="Current inbox",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=other_task.id,
        subject="Other inbox",
    )
    session.add_all([current_thread, other_thread])
    session.flush()
    current_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=current_task.id,
        thread_id=current_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Current scoped inbox message.",
    )
    other_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=other_task.id,
        thread_id=other_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Other task message should not appear.",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=current_task.id,
        agent_profile_id=recipient.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["get_agent_inbox"],
            }
        },
    )
    session.add_all([current_message, other_message, run])
    session.commit()

    result = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=current_task.id,
            run_id=run.id,
            allowed_tools=("get_agent_inbox",),
            metadata={"agent_mailbox": {"scope": {"task_id": str(current_task.id)}}},
        ),
        tool_name="get_agent_inbox",
        arguments={"latest_limit": 5, "unread_only": True},
    )

    assert result.status == "completed"
    assert result.output is not None
    assert result.output["thread_count"] == 1
    assert result.output["message_count"] == 1
    assert result.output["unread_count"] == 1
    assert result.output["latest_messages"][0]["id"] == str(current_message.id)
    serialized_output = str(result.output)
    assert str(other_message.id) not in serialized_output
    assert "Other task message" not in serialized_output


def test_backend_tool_executor_scopes_mark_read_to_runtime_metadata() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    current_task = Task(workspace_id=workspace.id, title="Current task")
    other_task = Task(workspace_id=workspace.id, title="Other task")
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    session.add_all([current_task, other_task, sender, recipient])
    session.flush()
    current_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=current_task.id,
        subject="Current inbox",
    )
    other_thread = AgentMessageThread(
        workspace_id=workspace.id,
        task_id=other_task.id,
        subject="Other inbox",
    )
    session.add_all([current_thread, other_thread])
    session.flush()
    other_message = AgentMessage(
        workspace_id=workspace.id,
        task_id=other_task.id,
        thread_id=other_thread.id,
        sender_agent_profile_id=sender.id,
        recipient_agent_profile_id=recipient.id,
        message_type="handoff",
        body="Other task message should not be marked read.",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=current_task.id,
        agent_profile_id=recipient.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["mark_agent_message_read"],
            }
        },
    )
    session.add_all([other_message, run])
    session.commit()

    result = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=current_task.id,
            run_id=run.id,
            allowed_tools=("mark_agent_message_read",),
            metadata={"agent_mailbox": {"scope": {"task_id": str(current_task.id)}}},
        ),
        tool_name="mark_agent_message_read",
        arguments={"message_id": str(other_message.id)},
    )

    session.refresh(other_message)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "product_tool_failed"
    assert other_message.status == "sent"
    assert other_message.read_at is None


def test_backend_tool_executor_dispatches_workspace_memory_product_tools() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, title="Task")
    agent = AgentProfile(workspace_id=workspace.id, name="Researcher", role="researcher")
    existing = WorkspaceMemoryEntry(
        workspace_id=workspace.id,
        entry_type="note",
        title="Launch positioning",
        content="The company positioning is durable multi-agent operations.",
        tags=["strategy"],
        visibility_scope="workspace",
        importance=3,
        status="active",
    )
    session.add_all([task, agent, existing])
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_profile_id=agent.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": [
                    "search_workspace_memory",
                    "remember_workspace_memory",
                    "archive_workspace_memory",
                ],
            }
        },
    )
    session.add(run)
    session.commit()
    executor = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter())
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
        allowed_tools=(
            "search_workspace_memory",
            "remember_workspace_memory",
            "archive_workspace_memory",
        ),
    )

    searched = executor.execute_tool(
        context=context,
        tool_name="search_workspace_memory",
        arguments={"query": "multi-agent operations", "limit": 5},
    )
    remembered = executor.execute_tool(
        context=context,
        tool_name="remember_workspace_memory",
        arguments={
            "title": "Runtime lesson",
            "content": "Use persistent sessions for team operations.",
            "entry_type": "lesson",
            "tags": ["runtime", "team"],
            "importance": 7,
        },
    )
    assert remembered.output is not None
    archived = executor.execute_tool(
        context=context,
        tool_name="archive_workspace_memory",
        arguments={"memory_entry_id": remembered.output["id"]},
    )

    stored = session.get(WorkspaceMemoryEntry, UUID(str(remembered.output["id"])))
    assert searched.status == "completed"
    assert searched.output is not None
    assert searched.output["items"][0]["title"] == "Launch positioning"
    assert remembered.status == "completed"
    assert remembered.metadata["provenance"] == "backend_tool_executor"
    assert remembered.metadata["tool_kind"] == "product"
    assert remembered.output["entry_type"] == "lesson"
    assert archived.status == "completed"
    assert archived.output is not None
    assert archived.output["status"] == "archived"
    assert stored is not None
    assert stored.created_by_agent_run_id == run.id
    assert stored.created_by_agent_profile_id == agent.id
    assert stored.status == "archived"


def test_backend_tool_executor_queues_self_hosted_stdio_mcp_job() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_provider="self_hosted",
        runtime_type="self_hosted",
        name="local-node",
    )
    task = Task(workspace_id=workspace.id, title="Task")
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": "mcp-image"},
    )
    session.add_all([runtime, task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.commit()

    result = BackendToolExecutor.for_mcp_adapter(session, StaticMcpAdapter()).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=("generate_image",),
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
    )
    job = session.query(SelfHostedMcpJob).one()

    assert result.status == "waiting_self_hosted"
    assert result.output is not None
    assert result.output["mcp_job_id"] == str(job.id)
    assert job.request_payload["contract_version"] == 1
    assert job.request_payload["transport"] == "stdio"
    assert job.request_payload["sdk"] == {
        "package": "mcp",
        "entrypoint": "mcp.client.stdio.stdio_client",
    }
    assert job.request_payload["request"] == {
        "contract_version": 1,
        "client": {
            "package": "mcp",
            "entrypoint": "mcp.client.stdio.stdio_client",
        },
        "server": {"command": "mcp-image", "args": []},
        "tool": {
            "name": "generate_image",
            "arguments": {"prompt": "mountain"},
            "timeout_seconds": 30,
        },
    }


def test_backend_tool_executor_routes_docker_stdio_mcp_to_bound_runtime() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        runtime_provider="cloud_docker",
        runtime_type="docker",
        name="team-runtime",
        docker_container_id="container-123",
        limits={"timeout_seconds": 11},
    )
    task = Task(workspace_id=workspace.id, title="Task")
    server = McpServer(
        workspace_id=workspace.id,
        name="image-tools",
        server_type="stdio",
        connection={"command": ["mcp-image", "--stdio"]},
    )
    session.add_all([runtime, task, server])
    session.flush()
    allow = McpToolAllowlist(
        workspace_id=workspace.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
    )
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime.id,
        input={
            "authorization_snapshot": {
                "workspace_id": str(workspace.id),
                "allowed_tools": ["generate_image"],
            }
        },
    )
    session.add_all([allow, run])
    session.commit()
    docker = RecordingDockerClient(
        [
            RuntimeCommandResult(
                exit_code=0,
                stdout=(
                    '{"status":"ready","contract_version":1,"sdk_package":"mcp",'
                    '"sdk_version":"1.27.1","stdio_client":"available",'
                    '"client_session":"available"}'
                ),
                stderr="",
            ),
            RuntimeCommandResult(
                exit_code=0,
                stdout='{"structuredContent":{"asset_id":"img_123"}}',
                stderr="",
            ),
        ]
    )

    result = BackendToolExecutor.for_mcp_adapter(
        session,
        StaticMcpAdapter(),
        docker_client=docker,
    ).execute_tool(
        context=AgentRuntimeContext(
            workspace_id=workspace.id,
            task_id=task.id,
            run_id=run.id,
            allowed_tools=("generate_image",),
        ),
        tool_name="generate_image",
        arguments={"prompt": "mountain"},
    )

    assert result.status == "completed"
    assert result.output == {"asset_id": "img_123"}
    assert docker.exec_calls[0]["command"] == [
        "python",
        "-m",
        "opsmesh_runtime.mcp_stdio_client",
        "--check",
    ]
    assert docker.exec_calls[1]["container_id"] == "container-123"
    assert docker.exec_calls[1]["timeout_seconds"] == 11
    command = docker.exec_calls[1]["command"]
    assert command[:3] == ["python", "-m", "opsmesh_runtime.mcp_stdio_client"]
    assert '"generate_image"' in command[3]


def test_waiting_runtime_status_transition_is_allowed() -> None:
    from backend.app.runs.status import can_transition_run

    assert can_transition_run(RunStatus.RUNNING, RunStatus.WAITING_RUNTIME)
    assert can_transition_run(RunStatus.WAITING_RUNTIME, RunStatus.QUEUED)


class StaticMcpAdapter:
    def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[object],
        timeout_seconds: int,
    ) -> dict[str, object]:
        return {"ok": True, "tool": tool_name}


class RecordingDockerClient:
    def __init__(self, command_results: list[RuntimeCommandResult]) -> None:
        self._command_results = command_results
        self.exec_calls: list[dict[str, object]] = []

    def create_container(self, request: object) -> str:
        return "container-123"

    def start_container(self, container_id: str) -> None:
        return None

    def stop_container(self, container_id: str) -> None:
        return None

    def remove_container(self, container_id: str) -> None:
        return None

    def remove_volume(self, volume_name: str) -> None:
        return None

    def exec_command(
        self,
        container_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> RuntimeCommandResult:
        self.exec_calls.append(
            {
                "container_id": container_id,
                "command": command,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._command_results.pop(0)


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
