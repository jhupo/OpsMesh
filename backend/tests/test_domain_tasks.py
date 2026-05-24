from collections.abc import Generator

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.domains.models import RevisionRequest
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "test-token"


def test_task_view_returns_domain_state_comments_and_revisions() -> None:
    queue = _queue()
    client, session = _client(queue)
    owner, workspace = _seed_workspace(session)

    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={
            "title": "写第一章",
            "domain_type": "novel",
            "generic_state": {"progress": 30},
            "domain_state": {"chapter": 1, "tone": "悬疑"},
        },
    )
    assert task.status_code == 201
    task_id = task.json()["id"]

    project = client.post(
        f"/api/v1/workspaces/{workspace.id}/domain-projects",
        headers=_headers(owner.id),
        json={"domain_type": "novel", "name": "长篇小说", "state": {"genre": "科幻"}},
    )
    assert project.status_code == 201

    item = client.post(
        f"/api/v1/workspaces/{workspace.id}/domain-items",
        headers=_headers(owner.id),
        json={
            "domain_project_id": project.json()["id"],
            "task_id": task_id,
            "item_type": "chapter",
            "title": "第一章",
            "content": {"outline": "主角醒来"},
        },
    )
    assert item.status_code == 201

    comment = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/review-comments",
        headers=_headers(owner.id),
        json={"domain_item_id": item.json()["id"], "body": "节奏太慢，开头要更抓人"},
    )
    assert comment.status_code == 201

    revision = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/revision-requests",
        headers=_headers(owner.id),
        json={
            "domain_item_id": item.json()["id"],
            "instruction": "重写开头三段，加强冲突",
            "payload": {"priority": "high"},
        },
    )
    assert revision.status_code == 201

    view = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/view",
        headers=_headers(owner.id),
    )

    assert view.status_code == 200
    body = view.json()
    assert body["task"]["domain_type"] == "novel"
    assert body["domain_project"]["name"] == "长篇小说"
    assert body["domain_items"][0]["content"] == {"outline": "主角醒来"}
    assert body["review_comments"][0]["body"] == "节奏太慢，开头要更抓人"
    assert body["revision_requests"][0]["instruction"] == "重写开头三段，加强冲突"
    assert session.query(RevisionRequest).count() == 1
    job = queue.dequeue()
    assert job is not None
    assert job.job_type == JobType.TASK_PLAN
    assert str(job.resource_id) == task_id


def test_task_view_redacts_sensitive_domain_metadata() -> None:
    queue = _queue()
    client, session = _client(queue)
    owner, workspace = _seed_workspace(session)

    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Domain metadata", "domain_type": "research"},
    )
    assert task.status_code == 201
    task_id = task.json()["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace.id}/domain-projects",
        headers=_headers(owner.id),
        json={
            "domain_type": "research",
            "name": "Research",
            "state": {"api_key": "sk-domain", "safe": "visible"},
        },
    )
    assert project.status_code == 201
    item = client.post(
        f"/api/v1/workspaces/{workspace.id}/domain-items",
        headers=_headers(owner.id),
        json={
            "domain_project_id": project.json()["id"],
            "task_id": task_id,
            "item_type": "note",
            "title": "Note",
            "content": {"base_url": "https://domain.example.test/private"},
            "state": {"headers": {"authorization": "Bearer hidden"}},
        },
    )
    assert item.status_code == 201
    comment = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/review-comments",
        headers=_headers(owner.id),
        json={
            "domain_item_id": item.json()["id"],
            "body": "Review",
            "metadata": {"token": "comment-token"},
        },
    )
    assert comment.status_code == 201
    revision = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/revision-requests",
        headers=_headers(owner.id),
        json={
            "domain_item_id": item.json()["id"],
            "instruction": "Revise",
            "payload": {"password": "hidden-password"},
        },
    )
    assert revision.status_code == 201

    view = client.get(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task_id}/view",
        headers=_headers(owner.id),
    )

    assert view.status_code == 200
    body = view.json()
    assert body["domain_project"]["state"] == {
        "api_key": "[redacted]",
        "safe": "visible",
    }
    assert body["domain_items"][0]["content"] == {"base_url": "[redacted]"}
    assert body["domain_items"][0]["state"] == {"headers": "[redacted]"}
    assert body["review_comments"][0]["metadata"] == {"token": "[redacted]"}
    assert body["revision_requests"][0]["payload"] == {"password": "[redacted]"}
    assert "sk-domain" not in str(body)
    assert "domain.example.test/private" not in str(body)
    assert "comment-token" not in str(body)
    assert "hidden-password" not in str(body)


def test_domain_item_cannot_be_used_across_workspaces() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, email="owner@example.com", slug="owner")
    other, other_workspace = _seed_workspace(session, email="other@example.com", slug="other")

    task = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks",
        headers=_headers(owner.id),
        json={"title": "Owner task"},
    )
    assert task.status_code == 201

    other_task = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/tasks",
        headers=_headers(other.id),
        json={"title": "Other task"},
    )
    assert other_task.status_code == 201

    other_item = client.post(
        f"/api/v1/workspaces/{other_workspace.id}/domain-items",
        headers=_headers(other.id),
        json={
            "task_id": other_task.json()["id"],
            "item_type": "chapter",
            "title": "Other item",
        },
    )
    assert other_item.status_code == 201

    denied = client.post(
        f"/api/v1/workspaces/{workspace.id}/tasks/{task.json()['id']}/review-comments",
        headers=_headers(owner.id),
        json={"domain_item_id": other_item.json()["id"], "body": "越权评论"},
    )

    assert denied.status_code == 400


def _client(queue: RedisQueue | None = None) -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    app = create_app(Settings(environment="test", log_format="text", internal_api_token=TOKEN))

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    if queue is not None:
        app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session


def _seed_workspace(
    session: Session,
    *,
    email: str = "owner@example.com",
    slug: str = "owner",
) -> tuple[User, Workspace]:
    user = User(email=email, display_name=email.split("@")[0])
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
