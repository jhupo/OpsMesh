from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.contracts import AgentRuntimeContext
from backend.app.agent_runtime.tools import BackendToolExecutor
from backend.app.capabilities.models import McpServer, McpToolAllowlist
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun
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
