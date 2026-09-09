from collections.abc import Generator
from dataclasses import replace

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import JSON

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.memory.semantic import (
    AgentSemanticMemoryService,
    SemanticMemoryConflictError,
    SemanticMemoryUpsert,
)
from backend.app.redis.dependencies import get_redis_client
from backend.app.teams.models import AgentTeam
from backend.app.tools.workspace_memory import WorkspaceMemorySearchService
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-internal-token"


def test_semantic_memory_is_scoped_versioned_idempotent_and_searchable() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, "acme")
    team = AgentTeam(workspace_id=workspace.id, name="Operations", team_type="operations")
    session.add(team)
    session.flush()
    service = AgentSemanticMemoryService(session)
    first_command = SemanticMemoryUpsert(
        workspace_id=workspace.id,
        scope_type="team",
        scope_id=team.id,
        memory_key="deployment-policy",
        knowledge_type="policy",
        title="Deployment policy token=hidden-title",
        content="Production deploys require approval.\n\ntoken=hidden-content",
        tags=["Deployment", "Policy"],
        importance=90,
        metadata={"api_key": "hidden-key", "owner": "operations"},
        expected_revision=0,
        changed_by_user_id=user.id,
        change_reason="Initial policy",
    )

    first = service.upsert(first_command)
    repeated = service.upsert(first_command)
    second = service.upsert(
        replace(
            first_command,
            content="Production deploys require two approvals.",
            expected_revision=1,
            change_reason="Require two reviewers",
        )
    )
    history = service.history(workspace_id=workspace.id, memory_entry_id=first.id)
    hits = WorkspaceMemorySearchService(session).search(
        workspace_id=workspace.id,
        query="two approvals",
        limit=5,
        source_types={"workspace_memory"},
    )

    assert repeated is first
    assert second.id == first.id
    assert second.revision == 2
    assert second.scope_type == "team"
    assert second.scope_id == str(team.id)
    assert second.tags == ["deployment", "policy"]
    assert second.memory_metadata == {"api_key": "[redacted]", "owner": "operations"}
    assert "\n\n" in history[1].snapshot["content"]
    assert [version.revision for version in history] == [2, 1]
    assert history[0].snapshot["content"] == "Production deploys require two approvals."
    assert hits[0]["metadata"]["scope_type"] == "team"
    assert hits[0]["metadata"]["scope_id"] == str(team.id)


def test_semantic_memory_rejects_stale_revision_and_foreign_scope() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, "acme")
    _, other_workspace = _seed_workspace(session, "other")
    foreign_team = AgentTeam(
        workspace_id=other_workspace.id,
        name="Foreign",
        team_type="operations",
    )
    session.add(foreign_team)
    session.flush()
    service = AgentSemanticMemoryService(session)
    command = SemanticMemoryUpsert(
        workspace_id=workspace.id,
        scope_type="workspace",
        scope_id=workspace.id,
        memory_key="region",
        knowledge_type="configuration",
        title="Primary region",
        content="ap-southeast-1",
        tags=[],
        importance=60,
        metadata={},
        changed_by_user_id=user.id,
    )
    entry = service.upsert(command)
    service.upsert(replace(command, content="eu-west-1", expected_revision=1))

    with pytest.raises(SemanticMemoryConflictError, match="revision is 2"):
        service.upsert(replace(command, content="us-east-1", expected_revision=1))
    with pytest.raises(ValueError, match="not found in the workspace"):
        service.upsert(
            replace(
                command,
                scope_type="team",
                scope_id=foreign_team.id,
                memory_key="foreign",
                expected_revision=0,
            )
        )
    with pytest.raises(SemanticMemoryConflictError, match="revision is 2"):
        service.archive(entry, expected_revision=1, changed_by_user_id=user.id)

    archived = service.archive(entry, expected_revision=2, changed_by_user_id=user.id)
    assert archived.status == "archived"
    assert archived.revision == 3
    assert [
        version.revision
        for version in service.history(
            workspace_id=workspace.id,
            memory_entry_id=entry.id,
        )
    ] == [3, 2, 1]


def test_semantic_memory_api_manages_current_head_and_version_history() -> None:
    client, session = _client()
    user, workspace = _seed_workspace(session, "api-memory")
    payload = {
        "scope_type": "workspace",
        "memory_key": "support-policy",
        "knowledge_type": "policy",
        "title": "Support policy",
        "content": "Escalate priority customers within one hour.",
        "tags": ["support"],
        "importance": 80,
        "expected_revision": 0,
    }

    created = client.post(
        f"/api/v1/workspaces/{workspace.id}/memories/semantic",
        headers=_headers(user.id),
        json=payload,
    )
    memory_id = created.json()["id"]
    updated = client.post(
        f"/api/v1/workspaces/{workspace.id}/memories/semantic",
        headers=_headers(user.id),
        json={
            **payload,
            "content": "Escalate priority customers within thirty minutes.",
            "expected_revision": 1,
        },
    )
    listed = client.get(
        f"/api/v1/workspaces/{workspace.id}/memories/semantic",
        headers=_headers(user.id),
    )
    versions = client.get(
        f"/api/v1/workspaces/{workspace.id}/memories/semantic/{memory_id}/versions",
        headers=_headers(user.id),
    )
    archived = client.post(
        f"/api/v1/workspaces/{workspace.id}/memories/semantic/{memory_id}/archive",
        headers=_headers(user.id),
        json={"expected_revision": 2, "change_reason": "Policy retired"},
    )

    assert created.status_code == 200
    assert created.json()["revision"] == 1
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert versions.status_code == 200
    assert [item["revision"] for item in versions.json()] == [2, 1]
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["revision"] == 3


def _session() -> Session:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = JSON()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _client() -> tuple[TestClient, Session]:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = JSON()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = session_factory()
    app = create_app(Settings(environment="test", internal_api_token=TOKEN))

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_redis_client] = lambda: fakeredis.FakeRedis(
        decode_responses=True
    )
    return TestClient(app), session


def _seed_workspace(session: Session, slug: str) -> tuple[User, Workspace]:
    user = User(email=f"{slug}@example.com", display_name=slug.title())
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}
