
from datetime import UTC, datetime

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
from backend.app.tasks.models import Task, TaskMessage, TaskStep
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


def test_workspace_memory_search_returns_workspace_scoped_matches() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Customer onboarding research",
        description="Collect customer notes and extract renewal risks.",
    )
    run = AgentRun(workspace_id=workspace.id, task_id=task.id)
    session.add_all([task, run])
    session.flush()
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="customer-notes.txt",
        content_type="text/plain",
        size_bytes=120,
        checksum_sha256="c" * 64,
        storage_key="workspaces/acme/files/customer-notes.txt",
        file_metadata={"summary": "Enterprise customer notes about onboarding blockers"},
    )
    other_file = WorkspaceFile(
        workspace_id=other_workspace.id,
        filename="customer-notes-secret.txt",
        content_type="text/plain",
        size_bytes=64,
        checksum_sha256="d" * 64,
        storage_key="workspaces/other/files/customer-notes-secret.txt",
        file_metadata={"summary": "Secret customer notes from another workspace"},
    )
    artifact = Artifact(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        artifact_type="report",
        filename="renewal-risk-report.pdf",
        content_type="application/pdf",
        size_bytes=2048,
        checksum_sha256="e" * 64,
        storage_key="workspaces/acme/artifacts/renewal-risk-report.pdf",
        artifact_metadata={"summary": "Customer renewal risk report"},
        created_at=datetime.now(UTC),
    )
    message = TaskMessage(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        message_type="step.completed",
        sequence=1,
        body="Analyst summarized customer notes and flagged onboarding risk.",
        payload={"decision": "continue"},
    )
    session.add_all([file, other_file, artifact, message])
    session.commit()

    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"search_workspace_memory"}),
    )

    results = ProductToolService(session).search_workspace_memory(context, "customer notes")

    assert results
    assert {item["source_type"] for item in results} >= {
        "workspace_file",
        "task",
        "task_message",
    }
    assert all("secret" not in item["title"] for item in results)
    assert all(item["score"] > 0 for item in results)
    assert next(item for item in results if item["source_type"] == "workspace_file")[
        "metadata"
    ] == {
        "content_type": "text/plain",
        "size_bytes": 120,
    }


def test_workspace_memory_entries_can_be_written_searched_and_archived() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    run = AgentRun(workspace_id=workspace.id, task_id=task.id)
    session.add_all([task, run])
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset(
            {
                "remember_workspace_memory",
                "search_workspace_memory",
                "archive_workspace_memory",
            }
        ),
    )
    other_context = ToolContext(
        workspace_id=other_workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"search_workspace_memory"}),
    )
    service = ProductToolService(session)

    entry = service.remember_workspace_memory(
        context,
        title="Customer renewal playbook",
        content="Enterprise customers with onboarding blockers need executive follow-up.",
        entry_type="lesson",
        tags=["Customer", "Renewal", "customer"],
        source_type="task",
        source_id=str(task.id),
        importance=77,
        metadata={"segment": "enterprise"},
    )
    session.commit()

    results = service.search_workspace_memory(context, "renewal blockers")
    other_results = service.search_workspace_memory(other_context, "renewal blockers")
    archived = service.archive_workspace_memory(context, entry.id)
    session.commit()
    archived_results = service.search_workspace_memory(context, "renewal blockers")

    assert entry.workspace_id == workspace.id
    assert entry.tags == ["customer", "renewal"]
    assert entry.importance == 77
    assert results[0]["source_type"] == "workspace_memory"
    assert results[0]["source_id"] == str(entry.id)
    assert results[0]["metadata"]["entry_type"] == "lesson"
    assert other_results == []
    assert archived.status == "archived"
    assert all(item["source_id"] != str(entry.id) for item in archived_results)


def test_workspace_memory_search_respects_limit_and_source_filters() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Renewal research",
        description="Customer renewal blockers and onboarding notes.",
    )
    run = AgentRun(workspace_id=workspace.id, task_id=task.id)
    session.add_all([task, run])
    session.flush()
    session.add_all(
        [
            WorkspaceFile(
                workspace_id=workspace.id,
                uploaded_by_user_id=user.id,
                filename="renewal-notes.txt",
                content_type="text/plain",
                size_bytes=120,
                checksum_sha256="a" * 64,
                storage_key="workspaces/acme/files/renewal-notes.txt",
                file_metadata={"summary": "Customer renewal notes"},
            ),
            Artifact(
                workspace_id=workspace.id,
                task_id=task.id,
                agent_run_id=run.id,
                artifact_type="report",
                filename="renewal-report.pdf",
                content_type="application/pdf",
                size_bytes=2048,
                checksum_sha256="b" * 64,
                storage_key="workspaces/acme/artifacts/renewal-report.pdf",
                artifact_metadata={"summary": "Customer renewal report"},
                created_at=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"search_workspace_memory"}),
    )
    service = ProductToolService(session)

    limited = service.search_workspace_memory(context, "renewal customer", limit=1)
    files_only = service.search_workspace_memory(
        context,
        "renewal customer",
        source_types={"workspace_file"},
    )
    none = service.search_workspace_memory(
        context,
        "renewal customer",
        source_types={"workspace_memory"},
    )

    assert len(limited) == 1
    assert files_only
    assert {item["source_type"] for item in files_only} == {"workspace_file"}
    assert none == []


def test_workspace_memory_write_requires_tool_permission() -> None:
    session = _session()
    _, workspace = _seed_workspace(session)
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"search_workspace_memory"}),
    )

    try:
        ProductToolService(session).remember_workspace_memory(
            context,
            title="Nope",
            content="Should not be stored.",
        )
    except ToolPermissionError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected missing memory write permission to fail")


def test_write_artifact_versions_are_bound_to_work_package() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Draft report",
        work_package_id="research-1",
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
    )
    session.add(run)
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"write_artifact"}),
    )
    service = ProductToolService(session)

    first = service.write_artifact(
        context,
        filename="report-v1.pdf",
        content=b"v1",
        content_type="application/pdf",
    )
    second = service.write_artifact(
        context,
        filename="report-v2.pdf",
        content=b"v2",
        content_type="application/pdf",
    )

    assert first.task_step_id == step.id
    assert first.work_package_id == "research-1"
    assert first.version == 1
    assert first.supersedes_artifact_id is None
    assert first.review_status == "pending"
    assert second.task_step_id == step.id
    assert second.work_package_id == "research-1"
    assert second.version == 2
    assert second.supersedes_artifact_id == first.id


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
