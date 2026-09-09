
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_messages.models import AgentMessage
from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.memory.indexing import WorkspaceMemoryIndexingService
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.memory.search import MemorySearchHit, MemorySearchRequest
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.tools.context import ToolContext
from backend.app.tools.errors import ToolPermissionError, ToolResourceNotFoundError
from backend.app.tools.product_tools.service import ProductToolService
from backend.app.tools.workspace_memory import WorkspaceMemorySearchService
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_product_tools_enforce_permissions_and_workspace_scope(tmp_path: Path) -> None:
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
        checksum_sha256=sha256(b"brief").hexdigest(),
        storage_key=f"workspaces/{workspace.id}/files/brief.txt",
    )
    other_file = WorkspaceFile(
        workspace_id=other_workspace.id,
        filename="secret.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum_sha256="b" * 64,
        storage_key="workspaces/other/files/secret.txt",
    )
    denied_file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="restricted.txt",
        content_type="text/plain",
        size_bytes=6,
        checksum_sha256="c" * 64,
        storage_key=f"workspaces/{workspace.id}/files/restricted.txt",
        sensitivity="restricted",
        runtime_access="denied",
    )
    session.add_all([task, run, file, other_file, denied_file])
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
    storage = LocalStorage(str(tmp_path / "storage"))
    storage.write(file.storage_key, b"brief")
    service = ProductToolService(session, storage=storage)

    assert [item.filename for item in service.list_workspace_files(context)] == ["brief.txt"]
    read = service.read_workspace_file(context, file.id)
    assert read.file.filename == "brief.txt"
    assert read.content == "brief"

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
    assert all(item["search_backend"] == "lexical" for item in results)
    assert all(item["resource_type"] == item["source_type"] for item in results)
    assert all(item["resource_id"] == item["source_id"] for item in results)


def test_workspace_memory_search_uses_pluggable_backend_with_workspace_scope() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Renewal research",
        description="Customer renewal blockers.",
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=user.id,
        title="Secret renewal research",
        description="Secret renewal blockers.",
    )
    session.add_all([task, other_task])
    session.commit()
    backend = _RecordingMemoryBackend()

    results = WorkspaceMemorySearchService(session, ranker=backend).search(
        workspace_id=workspace.id,
        query="renewal",
        limit=5,
        source_types={"task"},
    )

    assert backend.request is not None
    assert backend.request.workspace_id == workspace.id
    assert backend.request.source_types == {"task"}
    assert all(document.source_type == "task" for document in backend.request.documents)
    assert all(document.source_id != other_task.id for document in backend.request.documents)
    assert results == [
        {
            "source_type": "task",
            "source_id": str(task.id),
            "resource_type": "task",
            "resource_id": str(task.id),
            "title": "Renewal research",
            "snippet": "backend snippet",
            "score": 0.91,
            "search_backend": "vector_test",
            "created_at": task.created_at.isoformat(),
            "metadata": {
                "status": "draft",
                "domain_type": "general",
                "agent_team_id": None,
            },
        }
    ]


def test_workspace_memory_search_falls_back_to_lexical_when_backend_has_no_hits() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Renewal fallback",
        description="Customer renewal blockers.",
    )
    session.add(task)
    session.commit()

    results = WorkspaceMemorySearchService(session, ranker=_EmptyMemoryBackend()).search(
        workspace_id=workspace.id,
        query="renewal",
        limit=5,
        source_types={"task"},
    )

    assert results
    assert results[0]["source_type"] == "task"
    assert results[0]["search_backend"] == "lexical"


def test_semantic_memory_can_be_versioned_searched_and_archived() -> None:
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
                "upsert_semantic_memory",
                "search_workspace_memory",
                "archive_semantic_memory",
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

    entry = service.upsert_semantic_memory(
        context,
        scope_type="workspace",
        scope_id=workspace.id,
        memory_key="customer-renewal-playbook",
        knowledge_type="procedure",
        title="Customer renewal playbook",
        content="Enterprise customers with onboarding blockers need executive follow-up.",
        tags=["Customer", "Renewal", "customer"],
        importance=77,
        metadata={"segment": "enterprise"},
    )
    session.commit()

    results = service.search_workspace_memory(context, "renewal blockers")
    other_results = service.search_workspace_memory(other_context, "renewal blockers")
    archived = service.archive_semantic_memory(
        context,
        entry.id,
        expected_revision=1,
    )
    session.commit()
    archived_results = service.search_workspace_memory(context, "renewal blockers")

    assert entry.workspace_id == workspace.id
    assert entry.tags == ["customer", "renewal"]
    assert entry.importance == 77
    assert results[0]["source_type"] == "workspace_memory"
    assert results[0]["source_id"] == str(entry.id)
    assert results[0]["metadata"]["entry_type"] == "semantic_procedure"
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


def test_workspace_memory_indexing_refreshes_deterministic_chunks() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Renewal research",
        description="alpha " * 260,
        final_output={"summary": "customer renewal blockers"},
    )
    other_task = Task(
        workspace_id=other_workspace.id,
        created_by_user_id=user.id,
        title="Secret renewal research",
        description="secret renewal data",
    )
    session.add_all([task, other_task])
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=None,
        allowed_tools=frozenset({"search_workspace_memory"}),
    )
    service = WorkspaceMemoryIndexingService(session)

    first = service.refresh_task(workspace_id=workspace.id, task_id=task.id)
    task.description = "beta customer renewal action plan"
    session.commit()
    second = service.refresh_task(workspace_id=workspace.id, task_id=task.id)
    service.refresh_task(workspace_id=other_workspace.id, task_id=other_task.id)
    session.commit()

    results = ProductToolService(session).search_workspace_memory(
        context,
        "beta renewal",
        source_types={"task"},
    )
    stale_results = ProductToolService(session).search_workspace_memory(
        context,
        "alpha",
        source_types={"task"},
    )
    chunks = session.scalars(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.source_type == "task",
            WorkspaceMemoryEntry.source_id == str(task.id),
        )
    ).all()

    assert first.source_type == "task"
    assert first.indexed_chunks >= 2
    assert first.archived_chunks == 0
    assert second.indexed_chunks == 1
    assert second.archived_chunks == first.indexed_chunks
    assert results
    assert results[0]["source_type"] == "task"
    assert results[0]["metadata"]["indexed"] is True
    assert all("secret" not in item["title"].lower() for item in results)
    assert stale_results == []
    assert {entry.status for entry in chunks} == {"active", "archived"}


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
        ProductToolService(session).upsert_semantic_memory(
            context,
            scope_type="workspace",
            scope_id=workspace.id,
            memory_key="denied",
            knowledge_type="fact",
            title="Nope",
            content="Should not be stored.",
        )
    except ToolPermissionError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected missing memory write permission to fail")


def test_agent_mailbox_tools_send_and_list_workspace_scoped_messages() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    _, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    other_agent = AgentProfile(workspace_id=other_workspace.id, name="Foreign", role="builder")
    session.add_all([task, sender, recipient, other_agent])
    session.flush()
    run = AgentRun(workspace_id=workspace.id, task_id=task.id, agent_profile_id=sender.id)
    session.add(run)
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"send_agent_message", "list_agent_thread_messages"}),
    )
    service = ProductToolService(session)

    sent = service.send_agent_message(
        context,
        recipient_agent_profile_id=recipient.id,
        subject="Implementation handoff",
        body="Please implement the persistence layer.",
        payload={"api_key": "sk-hidden", "scope": "backend"},
    )
    listed = service.list_agent_thread_messages(
        context,
        thread_id=UUID(str(sent["thread"]["id"])),
    )

    stored = session.query(AgentMessage).one()
    assert sent["message"]["sender_agent_profile_id"] == str(sender.id)
    assert sent["message"]["recipient_agent_profile_id"] == str(recipient.id)
    assert sent["message"]["payload"] == {"api_key": "[redacted]", "scope": "backend"}
    assert listed["total"] == 1
    assert listed["items"][0]["payload"] == {"api_key": "[redacted]", "scope": "backend"}
    assert stored.payload == {"api_key": "sk-hidden", "scope": "backend"}

    try:
        service.send_agent_message(
            context,
            recipient_agent_profile_id=other_agent.id,
            body="Cross workspace should fail.",
        )
    except ToolResourceNotFoundError as exc:
        assert "workspace" in str(exc)
    else:
        raise AssertionError("Expected cross-workspace agent message to fail")


def test_agent_mailbox_tools_require_task_team_membership() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, slug="acme")
    team = AgentTeam(workspace_id=workspace.id, name="Core Team", team_type="delivery")
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Task",
    )
    sender = AgentProfile(workspace_id=workspace.id, name="Planner", role="planner")
    recipient = AgentProfile(workspace_id=workspace.id, name="Builder", role="builder")
    outsider = AgentProfile(workspace_id=workspace.id, name="Outsider", role="builder")
    session.add_all([task, sender, recipient, outsider])
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=sender.id,
                team_role="planner",
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=recipient.id,
                team_role="builder",
            ),
        ]
    )
    run = AgentRun(workspace_id=workspace.id, task_id=task.id, agent_profile_id=sender.id)
    session.add(run)
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        allowed_tools=frozenset({"send_agent_message"}),
    )
    service = ProductToolService(session)

    sent = service.send_agent_message(
        context,
        recipient_agent_profile_id=recipient.id,
        body="Team handoff.",
    )
    try:
        service.send_agent_message(
            context,
            recipient_agent_profile_id=outsider.id,
            body="Outsider handoff.",
        )
    except ToolResourceNotFoundError as exc:
        assert "task team" in str(exc)
    else:
        raise AssertionError("Expected non-team recipient to fail")

    assert sent["message"]["recipient_agent_profile_id"] == str(recipient.id)


def test_write_artifact_versions_are_bound_to_work_package(tmp_path: Path) -> None:
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
    storage = LocalStorage(str(tmp_path / "storage"))
    service = ProductToolService(session, storage=storage)

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
    assert storage.read(first.storage_key) == b"v1"
    assert storage.read(second.storage_key) == b"v2"


def test_write_artifact_compensates_storage_when_database_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    _, workspace = _seed_workspace(session, slug="artifact-compensation")
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"write_artifact"}),
    )
    storage_root = tmp_path / "storage"
    storage = LocalStorage(str(storage_root))
    service = ProductToolService(session, storage=storage)

    def fail_commit() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(session, "commit", fail_commit)

    with pytest.raises(ValueError) as error:
        service.write_artifact(
            context,
            filename="report.txt",
            content=b"not committed",
            content_type="text/plain",
        )

    assert getattr(error.value, "code", None) == "artifact_database_write_failed"
    assert session.query(Artifact).count() == 0
    assert not any(path.is_file() for path in storage_root.rglob("*"))


def test_write_artifact_rolls_back_partial_storage_write(tmp_path: Path) -> None:
    session = _session()
    _, workspace = _seed_workspace(session, slug="artifact-storage-failure")
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"write_artifact"}),
    )
    storage_root = tmp_path / "storage"
    service = ProductToolService(
        session,
        storage=_PartialWriteFailureStorage(str(storage_root)),
    )

    with pytest.raises(ValueError) as error:
        service.write_artifact(
            context,
            filename="report.txt",
            content=b"partial",
            content_type="text/plain",
        )

    assert getattr(error.value, "code", None) == "artifact_storage_write_failed"
    assert session.query(Artifact).count() == 0
    assert not any(path.is_file() for path in storage_root.rglob("*"))


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


class _RecordingMemoryBackend:
    backend_name = "vector_test"

    def __init__(self) -> None:
        self.request: MemorySearchRequest | None = None

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        self.request = request
        if not request.documents:
            return []
        return [
            MemorySearchHit(
                document=request.documents[0],
                score=0.91,
                snippet="backend snippet",
                backend_name=self.backend_name,
            )
        ]


class _EmptyMemoryBackend:
    backend_name = "postgres_full_text"

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        return []


class _PartialWriteFailureStorage(LocalStorage):
    def write(self, storage_key: str, content: bytes) -> None:
        super().write(storage_key, content[:1])
        raise OSError("storage write failed")


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
