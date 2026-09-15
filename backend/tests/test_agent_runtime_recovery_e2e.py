from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import fakeredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.db.base import Base
from backend.app.core.redis.keys import RedisKeyBuilder
from backend.app.core.security.models import SecurityEvent
from backend.app.domains.agents.runtime.contracts import AgentRunRequest, AgentRunResult
from backend.app.domains.orchestration.runs.control import RunControlService
from backend.app.domains.orchestration.runs.execution import (
    RunExecutionDependencies,
    RunExecutionService,
)
from backend.app.domains.orchestration.runs.models import (
    AgentRun,
    RunEvent,
    authorization_snapshot_fingerprint,
)
from backend.app.domains.orchestration.runs.resources import RunResourceReservationService
from backend.app.domains.orchestration.runs.service import RunOrchestrationService
from backend.app.domains.orchestration.runs.state import RunStatus
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.tasks.state import TaskStatus
from backend.app.domains.orchestration.workflows.steps.scheduling_state import (
    mark_step_scheduling_blocked,
    mark_step_scheduling_runnable,
)
from backend.app.domains.workspace.teams.models import AgentTeam
from backend.app.observability.audit.models import AuditEvent
from backend.app.runtime.environment.models import WorkspaceRuntime
from backend.app.runtime.environment.spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.runtime.workers.contracts import JobPayload, JobType
from backend.app.runtime.workers.registry import WorkerJobHandler
from backend.app.runtime.workers.queue import RedisQueue, consume_once
from backend.tests.test_worker_run_execution import (
    _patch_portable_types_for_sqlite,
    _seed_workspace,
)


class CountingRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.calls += 1
        return AgentRunResult(final_output="completed-once")


class SlowRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        await asyncio.sleep(0.05)
        return AgentRunResult(final_output="should-time-out")


class ExplodingRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        raise AssertionError("denied runs must not invoke the model")


@pytest.fixture(autouse=True)
def approve_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.domains.workspace.reviews.model_request import ModelRequestReview
    from backend.app.domains.workspace.reviews.models import ResourceReview
    from backend.app.domains.workspace.reviews.service import ResourcePolicyReviewBuilder

    monkeypatch.setattr(
        ResourcePolicyReviewBuilder,
        "review_tool_execution",
        lambda self, **kwargs: ResourceReview(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        ),
    )
    monkeypatch.setattr(
        "backend.app.domains.workspace.reviews.model_request.ModelRequestReviewService.review_request",
        lambda self, **kwargs: ModelRequestReview(
            required=False,
            risk_level="low",
            reasons=["model_request.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        ),
    )


def test_duplicate_delivery_is_terminally_idempotent() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Duplicate delivery",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    queue = _queue()
    run = RunOrchestrationService(session, queue).create_queued_run_for_task(task)
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        requested_by_user_id=user.id,
        idempotency_key=f"agent.run:{workspace.id}:{run.id}",
    )
    assert queue.enqueue(job, force=True) is True
    assert queue.enqueue(job, force=True) is True
    runner = CountingRunner()
    handler = WorkerJobHandler(session, queue, agent_runner=runner)

    assert consume_once(queue, handler.handle) is True
    assert consume_once(queue, handler.handle) is True

    assert runner.calls == 1, (run.status, run.error, task.status)
    assert run.status == RunStatus.COMPLETED.value
    assert session.scalars(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "run.completed",
        )
    ).all()


def test_runtime_timeout_marks_run_failed_with_durable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Runtime timeout",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    service = RunExecutionService(
        session=session,
        dependencies=RunExecutionDependencies(
            lifecycle=_run_lifecycle(session),
        ),
        agent_runner=SlowRunner(),
    )
    monkeypatch.setattr(RunExecutionService, "_runtime_timeout_seconds", lambda _self, _run: 0.01)
    job = _job(workspace.id, run.id, user.id)

    result = service.run_agent_sync(job)

    assert result.status == RunStatus.FAILED.value
    assert result.error == {
        "code": "runtime_wall_time_exceeded",
        "message": "Run exceeded the authorized runtime wall-time limit",
        "retryable": True,
    }
    event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "runtime.limit.exceeded",
        )
    )
    audit = session.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.action == "runtime.limit.exceeded",
            AuditEvent.target_id == str(run.id),
        )
    )
    assert event is not None
    assert event.event_metadata["limit"] == "timeout_seconds"
    assert audit is not None


def test_network_denial_fails_worker_run_without_model_call() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime = WorkspaceRuntime(
        workspace_id=workspace.id,
        name="Network denied runtime",
        execution_mode="none",
        status="running",
        connection_status="online",
        network_policy={"disabled": False},
    )
    session.add(runtime)
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Network policy team",
        team_type="software",
        default_task_policy={"team_runtime": {"workspace_runtime_id": str(runtime.id)}},
    )
    session.add(team)
    session.flush()
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        agent_team_id=team.id,
        title="Network denied",
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    session.flush()
    snapshot = {
        "version": 2,
        "workspace_id": str(workspace.id),
        "task_id": str(task.id),
        "task_step_id": None,
        "agent_profile_id": None,
        "runtime_space_id": None,
        "allowed_tools": [],
        "capability_catalog": None,
        "file_scope": {"mode": "authorized_file_resources", "allowed_file_ids": []},
        "runtime_binding": {
            "mode": "team_runtime",
            "workspace_id": str(workspace.id),
            "workspace_runtime_id": str(runtime.id),
            "runtime_space_id": None,
            "capability_resource_ids": [],
            "network_disabled": True,
            "file_access_scope": {"mode": "gateway_only", "allowed_file_ids": []},
        },
    }
    snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        runtime_id=runtime.id,
        status=RunStatus.QUEUED.value,
        input={"authorization_snapshot": snapshot},
    )
    session.add(run)
    session.commit()

    result = RunExecutionService(
        session=session,
        dependencies=RunExecutionDependencies(lifecycle=_run_lifecycle(session)),
        agent_runner=ExplodingRunner(),
    ).run_agent_sync(_job(workspace.id, run.id, user.id))

    assert result.status == RunStatus.FAILED.value
    assert result.error["code"] == "runtime_network_policy_mismatch"
    assert session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "runtime.authorization_blocked",
        )
    ) is not None
    assert session.scalar(
        select(SecurityEvent).where(
            SecurityEvent.workspace_id == workspace.id,
            SecurityEvent.action == "agent_runtime.authorization_blocked",
            SecurityEvent.reason == "runtime_network_policy_mismatch",
        )
    ) is not None


def test_runtime_space_quota_denial_does_not_create_reservation() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    runtime_space = RuntimeSpace(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Saturated space",
        status="active",
    )
    session.add(runtime_space)
    session.flush()
    session.add(
        RuntimeSpaceQuota(
            workspace_id=workspace.id,
            runtime_space_id=runtime_space.id,
            quota_key="active_runs",
            limit_value=0,
            reserved_value=0,
            unit="count",
            status="active",
        )
    )
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Quota denied",
        status=TaskStatus.QUEUED.value,
        runtime_space_id=runtime_space.id,
    )
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Quota step",
        runtime_space_id=runtime_space.id,
        status="queued",
    )
    session.add(step)
    session.flush()
    blocked: list[tuple[UUID, str]] = []

    def mark_blocked(step: TaskStep, reason: str, metadata: dict[str, object] | None) -> None:
        blocked.append((step.id, reason))
        mark_step_scheduling_blocked(step, reason, priority_score=None)

    service = RunResourceReservationService(
        session=session,
        mark_step_scheduling_blocked=mark_blocked,
        mark_step_scheduling_runnable=mark_step_scheduling_runnable,
    )

    assert service.reserve_for_step(task, step, runtime_space_id=runtime_space.id) is None
    session.flush()

    assert blocked == [(step.id, "runtime_space_quota_exceeded:active_runs")]
    assert session.scalar(
        select(RuntimeSpaceQuota).where(
            RuntimeSpaceQuota.runtime_space_id == runtime_space.id,
            RuntimeSpaceQuota.quota_key == "active_runs",
        )
    ).reserved_value == 0


def test_worker_loss_fails_stale_run_and_releases_execution_state() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Worker lost",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
        input={},
        started_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(run)
    session.commit()

    summary = RunControlService(
        session=session,
        enqueue_run=lambda _run, _user_id: True,
    ).recover_stale_worker_runs(stale_after_seconds=900)

    assert summary.recovered_runs == 1
    assert summary.failed_runs == 1
    assert run.status == RunStatus.FAILED.value
    assert run.error["code"] == "stale_worker_run"
    assert task.status == TaskStatus.FAILED.value
    assert session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "run.recovered_failed",
        )
    ) is not None


def _job(workspace_id: UUID, run_id: UUID, user_id: UUID) -> JobPayload:
    return JobPayload(
        workspace_id=workspace_id,
        job_type=JobType.AGENT_RUN,
        resource_id=run_id,
        requested_by_user_id=user_id,
        idempotency_key=f"agent.run:{workspace_id}:{run_id}",
    )


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        tracing_enabled=False,
    )


def _run_lifecycle(session: Session):
    from backend.tests.test_worker_run_execution import _run_lifecycle as build_lifecycle

    return build_lifecycle(session)


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()
