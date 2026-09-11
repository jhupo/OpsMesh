from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agent_runtime.core.contracts import (
    AgentRuntimeContext,
    AgentRuntimeInterruption,
    AgentRuntimeResumeState,
)
from backend.app.agent_runtime.state_store import AgentRunStateStore
from backend.app.approvals.agent_tool_interruptions import AgentToolInterruptionService
from backend.app.approvals.decisions import ApprovalDecisionService
from backend.app.approvals.lifecycle import AgentToolApprovalLifecycleService
from backend.app.approvals.models import Approval, PendingToolInvocation
from backend.app.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from backend.app.approvals.queries import ApprovalQueryService
from backend.app.approvals.service import ApprovalService
from backend.app.observability.audit_models import AuditEvent
from backend.app.capabilities.models import McpServer
from backend.app.core.config import Settings, get_settings
from backend.app.core.pagination import PageParams
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
from backend.app.secrets.service import SecretEncryptionService
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

    repeated = ApprovalDecisionService(session, queue).approve(approval, user.id, "retry")

    assert repeated.status == "approved"
    assert queue.dequeue() is None
    with pytest.raises(ValueError, match="not pending"):
        ApprovalDecisionService(session, queue).reject(approval, user.id, "conflict")


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


def test_pending_tool_invocation_encrypts_arguments_and_is_idempotent() -> None:
    session = _session()
    _, workspace, task, run = _seed_run(session)
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=None,
        approval_type="product.tool",
        risk_level="high",
        payload={"tool_name": "write_artifact"},
    )
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    service = PendingToolInvocationService(session, secrets)
    request = PendingToolInvocationRequest(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        approval_id=approval.id,
        tool_call_id="call-123",
        tool_name="write_artifact",
        tool_kind="product",
        arguments={"content": "private-token-value", "filename": "result.txt"},
        policy_decision={"risk_level": "high", "token": "must-not-persist"},
        idempotency_key=f"tool-approval:{run.id}:call-123",
    )

    first = service.create_or_get(request)
    second = service.create_or_get(request)
    session.commit()

    assert first.id == second.id
    assert "private-token-value" not in first.encrypted_arguments
    assert first.policy_decision["token"] == "[redacted]"
    assert service.arguments(workspace_id=workspace.id, invocation_id=first.id) == {
        "content": "private-token-value",
        "filename": "result.txt",
    }
    with pytest.raises(ValueError, match="not found"):
        service.arguments(workspace_id=uuid4(), invocation_id=first.id)


def test_pending_tool_invocation_rejects_idempotency_key_reuse() -> None:
    session = _session()
    _, workspace, task, run = _seed_run(session)
    approval = ApprovalService(session).create_approval(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        requested_by_agent_profile_id=None,
        approval_type="product.tool",
        risk_level="high",
        payload={},
    )
    service = PendingToolInvocationService(
        session,
        SecretEncryptionService(secret="pending-tool-secret", key_id="test-key"),
    )
    request = PendingToolInvocationRequest(
        workspace_id=workspace.id,
        task_id=task.id,
        agent_run_id=run.id,
        approval_id=approval.id,
        tool_call_id="call-123",
        tool_name="write_artifact",
        tool_kind="product",
        arguments={"filename": "first.txt"},
        policy_decision={"risk_level": "high"},
        idempotency_key="same-key",
    )
    service.create_or_get(request)

    with pytest.raises(ValueError, match="another tool invocation"):
        service.create_or_get(replace(request, arguments={"filename": "different.txt"}))


def test_approved_sdk_tool_invocation_is_resumable_and_executes_once() -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    AgentRunStateStore(session, secrets).save(
        workspace_id=workspace.id,
        run_id=run.id,
        state=AgentRuntimeResumeState(
            provider="openai_agents",
            serialized_state='{"$schemaVersion":"1.10"}',
        ),
    )
    context = AgentRuntimeContext(
        workspace_id=workspace.id,
        task_id=task.id,
        run_id=run.id,
    )
    AgentToolInterruptionService(session, secrets).persist(
        context=context,
        requested_by_agent_profile_id=None,
        interruptions=(
            AgentRuntimeInterruption(
                tool_call_id="call-approved",
                tool_name="write_artifact",
                tool_kind="product",
                arguments={"filename": "result.txt", "content": "private-value"},
                policy_decision={
                    "decision": "require_approval",
                    "risk_level": "high",
                    "token": "must-not-leak",
                },
            ),
        ),
    )
    session.commit()
    approval = session.query(Approval).one()
    invocation = session.query(PendingToolInvocation).one()
    queue = _queue()

    ApprovalDecisionService(session, queue, secrets).approve(approval, user.id, "approved")
    decisions = PendingToolInvocationService(session, secrets).decisions_for_run(
        workspace_id=workspace.id,
        run_id=run.id,
    )
    claimed, cached = PendingToolInvocationService(session, secrets).claim_execution(
        workspace_id=workspace.id,
        run_id=run.id,
        tool_call_id="call-approved",
        tool_name="write_artifact",
        arguments={"filename": "result.txt", "content": "private-value"},
    )

    assert cached is None
    assert decisions[0].tool_call_id == "call-approved"
    assert decisions[0].status == "approved"
    assert approval.payload["pending_tool_invocation_id"] == str(invocation.id)
    assert approval.payload["policy_decision"]["token"] == "[redacted]"
    assert "private-value" not in invocation.encrypted_arguments
    assert claimed.attempt_count == 1
    PendingToolInvocationService(session, secrets).complete_execution(
        claimed,
        {
            "status": "completed",
            "output": {"artifact_id": "artifact-1"},
            "error": None,
            "metadata": {},
        },
    )

    replay, cached = PendingToolInvocationService(session, secrets).claim_execution(
        workspace_id=workspace.id,
        run_id=run.id,
        tool_call_id="call-approved",
        tool_name="write_artifact",
        arguments={"filename": "result.txt", "content": "private-value"},
    )

    assert replay.attempt_count == 1
    assert cached is not None
    assert cached["output"] == {"artifact_id": "artifact-1"}
    assert queue.dequeue() is not None


def test_rejected_sdk_tool_invocation_queues_state_resume_without_failing_run() -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    approval, invocation = _persist_sdk_interruption(
        session, workspace.id, task.id, run.id, secrets
    )
    queue = _queue()

    ApprovalDecisionService(session, queue, secrets).reject(approval, user.id, "not allowed")
    decisions = PendingToolInvocationService(session, secrets).decisions_for_run(
        workspace_id=workspace.id,
        run_id=run.id,
    )

    assert approval.status == "rejected"
    assert invocation.status == "rejected"
    assert run.status == RunStatus.WAITING_APPROVAL.value
    assert task.status == TaskStatus.WAITING_APPROVAL.value
    assert decisions[0].status == "rejected"
    assert decisions[0].reason == "not allowed"
    assert queue.dequeue() is not None
    PendingToolInvocationService(session, secrets).mark_rejections_consumed(
        workspace_id=workspace.id,
        run_id=run.id,
    )
    assert invocation.status == "rejection_consumed"
    assert not PendingToolInvocationService(session, secrets).decisions_for_run(
        workspace_id=workspace.id,
        run_id=run.id,
    )


def test_pending_sdk_tool_timeout_fails_closed_with_audit_evidence() -> None:
    session = _session()
    _, workspace, task, run = _seed_run(session)
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    approval, invocation = _persist_sdk_interruption(
        session, workspace.id, task.id, run.id, secrets
    )
    approval.created_at = datetime.now(UTC) - timedelta(hours=2)
    session.commit()

    summary = AgentToolApprovalLifecycleService(session).expire_pending(
        timeout_seconds=3_600,
        now=datetime.now(UTC),
    )
    audit = session.query(AuditEvent).one()

    assert summary.expired == 1
    assert approval.status == "timed_out"
    assert invocation.status == "timed_out"
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None and run.error["code"] == "approval_timeout"
    assert task.status == TaskStatus.FAILED.value
    assert audit.action == "approval.timed_out"


def test_run_cancellation_closes_pending_sdk_tool_state() -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    approval, invocation = _persist_sdk_interruption(
        session, workspace.id, task.id, run.id, secrets
    )

    cancelled = AgentToolApprovalLifecycleService(session).cancel_for_run(
        workspace_id=workspace.id,
        run_id=run.id,
        actor_user_id=user.id,
    )
    restored = AgentRunStateStore(session, secrets).load(
        workspace_id=workspace.id,
        run_id=run.id,
    )

    assert cancelled == 1
    assert approval.status == "cancelled"
    assert invocation.status == "cancelled"
    assert restored is None


@pytest.mark.parametrize("operation", ["cancel", "expire"])
def test_approval_lifecycle_rejects_cross_workspace_join(operation: str) -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    _, foreign_workspace, _, _ = _seed_run(session)
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    approval, invocation = _persist_sdk_interruption(
        session, workspace.id, task.id, run.id, secrets
    )
    approval.workspace_id = foreign_workspace.id
    approval.created_at = datetime.now(UTC) - timedelta(hours=1)
    session.flush()

    lifecycle = AgentToolApprovalLifecycleService(session)
    if operation == "cancel":
        assert lifecycle.cancel_for_run(
            workspace_id=workspace.id, run_id=run.id, actor_user_id=user.id
        ) == 0
    else:
        assert lifecycle.expire_pending(timeout_seconds=60).expired == 0
    assert approval.status == "pending"
    assert invocation.status == "pending"


def test_claimed_tool_after_worker_loss_returns_unknown_without_replay() -> None:
    session = _session()
    user, workspace, task, run = _seed_run(session)
    secrets = SecretEncryptionService(secret="pending-tool-secret", key_id="test-key")
    approval, _ = _persist_sdk_interruption(
        session, workspace.id, task.id, run.id, secrets
    )
    ApprovalDecisionService(session, secrets=secrets).approve(approval, user.id)
    pending = PendingToolInvocationService(session, secrets)
    invocation, cached = pending.claim_execution(
        workspace_id=workspace.id,
        run_id=run.id,
        tool_call_id="call-lifecycle",
        tool_name="write_artifact",
        arguments={"content": "private"},
    )
    assert cached is None

    changed = pending.mark_stale_execution_outcome_unknown(
        workspace_id=workspace.id,
        run_id=run.id,
    )
    replay, cached = pending.claim_execution(
        workspace_id=workspace.id,
        run_id=run.id,
        tool_call_id="call-lifecycle",
        tool_name="write_artifact",
        arguments={"content": "private"},
    )

    assert changed == 1
    assert replay.status == "outcome_unknown"
    assert replay.attempt_count == 1
    assert cached is not None
    assert cached["error"]["code"] == "tool_execution_outcome_unknown"


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


def _persist_sdk_interruption(
    session: Session,
    workspace_id: UUID,
    task_id: UUID,
    run_id: UUID,
    secrets: SecretEncryptionService,
) -> tuple[Approval, PendingToolInvocation]:
    AgentRunStateStore(session, secrets).save(
        workspace_id=workspace_id,
        run_id=run_id,
        state=AgentRuntimeResumeState(
            provider="openai_agents",
            serialized_state='{"$schemaVersion":"1.10"}',
        ),
    )
    AgentToolInterruptionService(session, secrets).persist(
        context=AgentRuntimeContext(
            workspace_id=workspace_id,
            task_id=task_id,
            run_id=run_id,
        ),
        requested_by_agent_profile_id=None,
        interruptions=(
            AgentRuntimeInterruption(
                tool_call_id="call-lifecycle",
                tool_name="write_artifact",
                tool_kind="product",
                arguments={"content": "private"},
                policy_decision={
                    "decision": "require_approval",
                    "risk_level": "high",
                },
            ),
        ),
    )
    session.commit()
    return session.query(Approval).one(), session.query(PendingToolInvocation).one()


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
