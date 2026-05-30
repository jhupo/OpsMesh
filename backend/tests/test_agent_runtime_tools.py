from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.contracts import AgentRuntimeContext
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.capabilities.models import McpServer, McpToolAllowlist, McpToolCallLog
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import RuntimeCommandResult
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.models import SelfHostedMcpJob
from backend.app.tasks.models import Task
from backend.app.tools.errors import ToolPermissionError
from backend.app.workspaces.models import Workspace, WorkspaceMember


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
    assert job.request_payload["command"] == ["mcp-image"]
    assert job.request_payload["jsonrpc"]["params"] == {
        "name": "generate_image",
        "arguments": {"prompt": "mountain"},
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
        RuntimeCommandResult(
            exit_code=0,
            stdout='{"jsonrpc":"2.0","id":"1","result":{"asset_id":"img_123"}}',
            stderr="",
        )
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
    assert docker.exec_calls[0]["container_id"] == "container-123"
    assert docker.exec_calls[0]["timeout_seconds"] == 11
    command = docker.exec_calls[0]["command"]
    assert command[:2] == ["mcp-image", "--stdio"]
    assert '"generate_image"' in command[2]


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
    def __init__(self, command_result: RuntimeCommandResult) -> None:
        self._command_result = command_result
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
        return self._command_result


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
