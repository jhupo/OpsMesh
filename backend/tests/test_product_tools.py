
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.artifacts.models import Artifact
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.files.models import WorkspaceFile
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolPermissionError, ToolResourceNotFoundError
from backend.app.tools.product_tools import ProductToolService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_product_tools_enforce_permissions_and_workspace_scope() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    run = AgentRun(workspace_id=workspace.id, task_id=task.id)
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="brief.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum_sha256="a" * 64,
        storage_key="workspaces/acme/files/brief.txt",
    )
    other_file = WorkspaceFile(
        workspace_id=other_workspace.id,
        filename="secret.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum_sha256="b" * 64,
        storage_key="workspaces/other/files/secret.txt",
    )
    session.add_all([task, run, file, other_file])
    session.commit()

    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset(
            {
                "list_workspace_files",
                "read_workspace_file",
                "search_workspace_memory",
                "write_artifact",
            }
        ),
    )
    service = ProductToolService(session)

    assert [item.filename for item in service.list_workspace_files(context)] == ["brief.txt"]
    assert service.read_workspace_file(context, file.id).filename == "brief.txt"

    try:
        service.read_workspace_file(context, other_file.id)
    except ToolResourceNotFoundError:
        pass
    else:
        raise AssertionError("Expected cross-workspace read to fail")

    artifact = service.write_artifact(
        context,
        filename="../report\r\n.txt",
        content=b"hello",
        content_type="text/plain",
    )
    assert service.search_workspace_memory(context, "customer notes") == []
    session.commit()

    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()

    assert artifact.workspace_id == workspace.id
    assert artifact.filename == "report__.txt"
    assert artifact.storage_key.endswith("/report__.txt")
    assert session.query(Artifact).count() == 1
    assert [event.event_type for event in events] == [
        "tool.called",
        "tool.completed",
        "tool.called",
        "tool.completed",
        "tool.called",
        "tool.called",
        "tool.completed",
        "tool.called",
        "tool.completed",
    ]


def test_product_tool_permission_denied() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset(),
    )

    try:
        ProductToolService(session).list_workspace_files(context)
    except ToolPermissionError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected missing tool permission to fail")


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


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
