from collections.abc import Generator
from uuid import uuid4

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.pagination import PageParams
from backend.app.approvals.decisions import ApprovalDecisionService
from backend.app.approvals.queries import ApprovalQueryService
from backend.app.approvals.service import ApprovalService
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings, get_settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.reviews.constants import (
    RESOURCE_STATUS_ACTIVE,
    RESOURCE_STATUS_PENDING_APPROVAL,
    RESOURCE_STATUS_REJECTED,
    REVIEW_TYPE_MCP_SERVER,
)
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workers.dependencies import get_worker_queue
from backend.app.workers.jobs import JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_approval_approve_enqueues_resume_job() -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
    )
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=None,
        approval_type="runtime.command",
        risk_level="high",
        payload={"command": ["echo", "ok"]},
    )
    session.commit()

    decided = ApprovalDecisionService(session, queue).approve(approval, user.id, "ok")
    job = queue.dequeue()

    assert decided.status == "approved"
    assert decided.decided_by_user_id == user.id
    assert job is not None
    assert job.job_type == JobType.AGENT_RUN
    assert job.resource_id == run.id


def test_approval_approve_api_enqueues_resume_job() -> None:
    queue = _queue()
    client, session = _api_client(queue)
    user, workspace, task, run = _seed_run(session)
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=None,
        approval_type="runtime.command",
        risk_level="high",
        payload={"command": ["echo", "ok"]},
    )
    session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/approvals/{approval.id}/approve",
        headers=_headers(user.id),
        json={"reason": "approved through API"},
    )
    job = queue.dequeue()

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert job is not None
    assert job.job_type == JobType.AGENT_RUN
    assert job.resource_id == run.id
    assert queue.dequeue() is None


def test_approval_reject_marks_run_and_task_failed() -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=None,
        approval_type="runtime.command",
        risk_level="high",
        payload={"command": ["rm", "-rf", "/"]},
    )
    session.commit()

    ApprovalDecisionService(session).reject(approval, user.id, "too risky")

    assert run.status == RunStatus.FAILED.value
    assert task.status == TaskStatus.FAILED.value
    assert run.error == {"code": "approval_rejected", "message": "Approval was rejected"}


def test_list_approvals_filters_by_status() -> None:
    session = _session()
    _, workspace, task, run = _seed_run(session)
    ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=None,
        approval_type="runtime.command",
        risk_level="high",
        payload={},
    )
    session.commit()

    items, total = ApprovalQueryService(session).list_approvals(
        workspace.id,
        PageParams(),
        status="pending",
    )

    assert total == 1
    assert items[0].status == "pending"


def test_approval_approve_activates_pending_resource_review_target() -> None:
    session = _session()
    user, workspace, _, _ = _seed_run(session)
    server = McpServer(
        workspace_id=workspace.id,
        name="Dangerous MCP",
        server_type="stdio",
        connection={"command": "npx", "args": ["danger"]},
        status=RESOURCE_STATUS_PENDING_APPROVAL,
    )
    session.add(server)
    session.flush()
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        requested_by_agent_profile_id=None,
        approval_type=REVIEW_TYPE_MCP_SERVER,
        risk_level="high",
        payload={
            "kind": "resource_review",
            "target_type": "mcp_server",
            "target_id": str(server.id),
        },
    )
    session.commit()

    ApprovalDecisionService(session).approve(approval, user.id, "trusted")

    assert server.status == RESOURCE_STATUS_ACTIVE


def test_approval_reject_marks_pending_resource_review_target_rejected() -> None:
    session = _session()
    user, workspace, _, _ = _seed_run(session)
    server = McpServer(
        workspace_id=workspace.id,
        name="Dangerous MCP",
        server_type="stdio",
        connection={"command": "npx", "args": ["danger"]},
        status=RESOURCE_STATUS_PENDING_APPROVAL,
    )
    session.add(server)
    session.flush()
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        requested_by_agent_profile_id=None,
        approval_type=REVIEW_TYPE_MCP_SERVER,
        risk_level="high",
        payload={
            "kind": "resource_review",
            "target_type": "mcp_server",
            "target_id": str(server.id),
        },
    )
    session.commit()

    ApprovalDecisionService(session).reject(approval, user.id, "too broad")

    assert server.status == RESOURCE_STATUS_REJECTED


def _seed_run(session: Session) -> tuple[User, Workspace, Task, AgentRun]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.flush()
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    task.status = TaskStatus.WAITING_APPROVAL.value
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        status=RunStatus.WAITING_APPROVAL.value,
    )
    session.add(run)
    session.commit()
    return user, workspace, task, run


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _api_client(queue: RedisQueue) -> tuple[TestClient, Session]:
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
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token="test-token",
            credential_encryption_secret="test-credential-secret",
            credential_encryption_key_id="test",
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_worker_queue] = lambda: queue
    return TestClient(app), session


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
    )


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": "Bearer test-token", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
