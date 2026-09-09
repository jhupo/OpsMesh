from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event
from uuid import UUID, uuid4

import fakeredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_messages.models import AgentMessage
from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRunResult
from backend.app.agent_runtime.sessions import PersistentAgentSession
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.models import McpServer, McpToolAllowlist, McpToolCallLog
from backend.app.core.config import Settings
from backend.app.core.request_context import current_log_context
from backend.app.core.trace_context import TraceContext, trace_context
from backend.app.costs.models import ModelPricingRule, ModelUsageRecord, WorkspaceCostBudget
from backend.app.db.base import Base
from backend.app.exports.models import WorkspaceExportJob
from backend.app.exports.status import WorkspaceExportJobStatus
from backend.app.identity.models import User
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.model_providers.credential_commands import ModelProviderCredentialCommandService
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.operations.worker_heartbeats import WorkerHeartbeatOperationsService
from backend.app.orchestration.run_authorization_snapshot import RunAuthorizationSnapshotService
from backend.app.orchestration.run_request_builder import RunRequestBuilder
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.reviews.model_request import ModelRequestReview
from backend.app.reviews.service import ResourceReview
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.runtime_manager.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
    RuntimeCommandResult,
    RuntimeCreateRequest,
)
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.collaboration_state import TaskCollaborationStateService
from backend.app.tasks.events import RedisTaskEventBus
from backend.app.tasks.models import Task, TaskEventOutbox, TaskMessage, TaskStep
from backend.app.tasks.status import TaskStatus
from backend.app.teams.execution_loop import TeamExecutionLoopQueueService
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.runtime import TeamRuntimeService
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workers.runner import WorkerMaintenanceSummary, WorkerRunner, WorkerRunnerConfig
from backend.app.workspaces.models import Workspace, WorkspaceMember


@pytest.fixture(autouse=True)
def approve_reviews_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resource_review(self, **kwargs):  # noqa: ANN001, ANN202
        return ResourceReview(
            required=False,
            risk_level="low",
            reasons=["llm_review.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        )

    def fake_model_request_review(self, **kwargs):  # noqa: ANN001, ANN202
        return ModelRequestReview(
            required=False,
            risk_level="low",
            reasons=["model_request.approved"],
            signals={"reviewer": "llm", "verdict": "approve"},
        )

    monkeypatch.setattr(
        "backend.app.reviews.service.ResourcePolicyReviewBuilder.review_tool_execution",
        fake_resource_review,
    )
    monkeypatch.setattr(
        "backend.app.reviews.model_request.ModelRequestReviewService.review_request",
        fake_model_request_review,
    )


class DeterministicAgentRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(final_output="deterministic_run_completed")


class ApprovingTeamAgentRunner:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        review_policy = request.context.metadata.get("review_policy")
        if isinstance(review_policy, dict) and review_policy.get("mode") == "final_acceptance":
            return AgentRunResult(
                final_output=json.dumps(
                    {
                        "decision": "approved",
                        "summary": "manager_approved_delivery",
                        "reasons": [],
                    }
                )
            )
        return AgentRunResult(final_output=f"completed_by:{request.agent_profile.name}")


class MustNotRunAgentRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.calls += 1
        raise AssertionError("model runner must not be called after budget exhaustion")


class FakeDockerClient(DockerRuntimeClient):
    def __init__(self) -> None:
        self.created_requests: list[RuntimeCreateRequest] = []
        self.started: list[str] = []

    def create_container(self, request: RuntimeCreateRequest) -> str:
        self.created_requests.append(request)
        return f"container-{len(self.created_requests)}"

    def start_container(self, container_id: str) -> None:
        self.started.append(container_id)

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
        *,
        input_file: RuntimeCommandInputFile | None = None,
    ) -> RuntimeCommandResult:
        _ = input_file
        return RuntimeCommandResult(exit_code=0, stdout="ok\n", stderr="")


def test_worker_runner_run_once_processes_agent_job() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    parent_trace = TraceContext(
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="abcdef0123456789",
    )
    with trace_context(parent_trace):
        queue.enqueue(
            JobPayload(
                workspace_id=workspace_id,
                job_type=JobType.AGENT_RUN,
                resource_id=run_id,
                requested_by_user_id=user_id,
                idempotency_key=f"agent.run:{workspace_id}:{run_id}",
                priority=7,
            )
        )
    queued_trace = queue.list_queued(workspace_id=workspace_id)[0].trace_context()
    assert queued_trace is not None
    assert queued_trace.trace_id == parent_trace.trace_id
    assert queued_trace.parent_span_id == parent_trace.span_id
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-1", queue_name="agent_runs"),
        agent_runner=DeterministicAgentRunner(),
    )

    assert runner.run_once() is True
    assert queue.count_processing() == 0

    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED.value
        assert run.output is not None
        assert "final_output" in run.output
        event = session.scalar(
            select(RunEvent).where(
                RunEvent.agent_run_id == run_id,
                RunEvent.event_type == "run.started",
            )
        )
        assert event is not None
        assert event.event_metadata["trace_id"] == parent_trace.trace_id
        assert event.event_metadata["parent_span_id"] == queued_trace.span_id
        usage = session.scalar(
            select(ModelUsageRecord).where(ModelUsageRecord.agent_run_id == run_id)
        )
        assert usage is not None
        assert usage.metering_status == "missing_usage"
        assert usage.trace_id == parent_trace.trace_id
        assert (
            session.scalar(
                select(RunEvent).where(
                    RunEvent.agent_run_id == run_id,
                    RunEvent.event_type == "cost.usage_recorded",
                )
            )
            is not None
        )


def test_worker_runner_loop_records_heartbeat_and_summary() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
            priority=7,
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-loop",
            queue_name="agent_runs",
            heartbeat_interval_seconds=0,
            idle_sleep_seconds=0,
        ),
        agent_runner=DeterministicAgentRunner(),
        sleep=lambda _: None,
    )

    summary = runner.run(max_jobs=1)

    assert summary.processed == 1
    assert summary.failed == 0
    assert summary.stopped is False
    with session_factory() as session:
        heartbeat = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "worker-loop")
        )
        node = session.scalar(select(WorkerNode).where(WorkerNode.worker_id == "worker-loop"))
        lease = session.scalar(select(WorkerLease).where(WorkerLease.worker_id == "worker-loop"))
        assert heartbeat is not None
        assert node is not None
        assert lease is not None
        assert heartbeat.status == "online"
        assert node.status == "online"
        assert lease.status == "completed"
        assert lease.lease_metadata["priority"] == 7
        assert [event["type"] for event in lease.lease_metadata["lifecycle_events"]] == [
            "claimed",
            "started",
            "completed",
        ]
        assert lease.lease_metadata["last_lifecycle_event"]["type"] == "completed"
        assert heartbeat.details["processed"] == 1
        assert heartbeat.details["failed"] == 0
        assert isinstance(heartbeat.details["trace_id"], str)
        assert isinstance(heartbeat.details["span_id"], str)
        run_events = session.scalars(
            select(RunEvent).where(RunEvent.agent_run_id == run_id).order_by(RunEvent.sequence)
        ).all()
        assert run_events
        run_trace_ids = {event.event_metadata["trace_id"] for event in run_events}
        assert len(run_trace_ids) == 1
        assert all(isinstance(event.event_metadata["span_id"], str) for event in run_events)


def test_worker_blocks_model_call_when_workspace_cost_budget_is_exhausted() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory, slug="budget-block")
    now = datetime.now(UTC)
    with session_factory() as session:
        historical_run = AgentRun(
            workspace_id=workspace_id,
            status=RunStatus.COMPLETED.value,
            input={},
            completed_at=now,
        )
        session.add(historical_run)
        session.flush()
        pricing = ModelPricingRule(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            provider="openai",
            model="gpt-4.1",
            version="budget-test-v1",
            currency="USD",
            input_rate_per_million=Decimal("0"),
            output_rate_per_million=Decimal("0"),
            request_rate=Decimal("0.02"),
            effective_from=now - timedelta(days=1),
            source="test",
        )
        session.add(pricing)
        session.flush()
        session.add_all(
            [
                WorkspaceCostBudget(
                    workspace_id=workspace_id,
                    currency="USD",
                    monthly_limit=Decimal("0.01"),
                    warning_ratio=Decimal("0.8"),
                    enforcement="block",
                    enabled=True,
                ),
                ModelUsageRecord(
                    workspace_id=workspace_id,
                    agent_run_id=historical_run.id,
                    provider="openai",
                    model="gpt-4.1",
                    pricing_rule_id=pricing.id,
                    pricing_version=pricing.version,
                    metering_status="priced",
                    job_attempt=0,
                    request_count=1,
                    input_tokens=1,
                    output_tokens=1,
                    cached_input_tokens=0,
                    reasoning_tokens=0,
                    total_tokens=2,
                    currency="USD",
                    input_cost=Decimal("0"),
                    output_cost=Decimal("0"),
                    cached_input_cost=Decimal("0"),
                    request_cost=Decimal("0.02"),
                    total_cost=Decimal("0.02"),
                    raw_usage={},
                    occurred_at=now,
                ),
            ]
        )
        session.commit()
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    model_runner = MustNotRunAgentRunner()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-budget", queue_name="agent_runs"),
        agent_runner=model_runner,
    )

    assert runner.run_once() is True

    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        assert run.status == RunStatus.FAILED.value
        assert model_runner.calls == 0
        assert (
            session.scalar(
                select(RunEvent).where(
                    RunEvent.agent_run_id == run_id,
                    RunEvent.event_type == "cost.budget_blocked",
                )
            )
            is not None
        )


def test_worker_runner_maintenance_reclaims_job_after_crash_before_lease() -> None:
    session_factory = _session_factory()
    queue = _queue(visibility_timeout_seconds=-1)
    workspace_id, run_id, user_id = _seed_run(session_factory, slug="crash-before-lease")
    job = JobPayload(
        workspace_id=workspace_id,
        job_type=JobType.AGENT_RUN,
        resource_id=run_id,
        requested_by_user_id=user_id,
        idempotency_key=f"agent.run:{workspace_id}:{run_id}",
    )
    queue.enqueue(job)
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-crash-before-lease",
            queue_name="agent_runs",
        ),
    )

    def crash_before_lease(_: JobPayload) -> None:
        raise SystemExit("crash before lease")

    runner._lease_reporter.start_lease = crash_before_lease  # type: ignore[method-assign]

    with pytest.raises(SystemExit, match="crash before lease"):
        runner.run_once()

    assert queue.count_queued() == 0
    assert queue.count_processing() == 1
    with session_factory() as session:
        assert session.query(WorkerLease).count() == 0

    runner.run_maintenance()

    assert queue.count_processing() == 0
    reclaimed = queue.dequeue()
    assert reclaimed is not None
    trace_fields = {"trace_id", "span_id", "parent_span_id"}
    assert reclaimed.model_dump(exclude=trace_fields) == job.model_dump(exclude=trace_fields)
    assert reclaimed.trace_id is not None
    assert reclaimed.span_id is not None


def test_multi_agent_handoff_survives_worker_restart_and_manager_approval() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, task_id, user_id = _seed_multi_agent_task(session_factory)

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        orchestration = RunOrchestrationService(session, queue)
        first_run = orchestration.create_queued_run_for_task(task)
        assert first_run is not None
        assert orchestration.enqueue_run(first_run, requested_by_user_id=user_id) is True
        session.commit()

    first_worker = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-before-restart", queue_name="agent_runs"),
        agent_runner=ApprovingTeamAgentRunner(),
    )
    assert first_worker.run_once() is True

    with session_factory() as session:
        state = TaskCollaborationStateService(session).get_state(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        assert state is not None
        phases = {phase["phase"]: phase for phase in state["phases"]}
        assert phases["manager_planning"]["status"] == "completed"
        assert phases["specialist_execution"]["status"] == "in_progress"
        assert phases["handoff"]["status"] == "running"
        assert any(item["status"] == "handoff_in_progress" for item in state["handoffs"])

    restarted_worker = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-after-restart", queue_name="agent_runs"),
        agent_runner=ApprovingTeamAgentRunner(),
    )
    recovered_jobs = 0
    while restarted_worker.run_once():
        recovered_jobs += 1

    assert recovered_jobs == 2
    assert queue.count_queued(workspace_id=workspace_id) == 0
    assert queue.count_processing(workspace_id=workspace_id) == 0

    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        runs = session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.created_at)
        ).all()
        decision = session.scalar(
            select(TaskMessage).where(
                TaskMessage.task_id == task_id,
                TaskMessage.message_type == "pm.acceptance_decision",
            )
        )
        leases = session.scalars(
            select(WorkerLease)
            .where(WorkerLease.workspace_id == workspace_id)
            .order_by(WorkerLease.started_at)
        ).all()
        state = TaskCollaborationStateService(session).get_state(
            workspace_id=workspace_id,
            task_id=task_id,
        )

        assert task.status == TaskStatus.COMPLETED.value
        assert task.final_output is not None
        assert task.final_output["final_output"] == "manager_approved_delivery"
        assert len(runs) == 3
        assert all(run.status == RunStatus.COMPLETED.value for run in runs)
        assert decision is not None
        assert decision.payload["decision"] == "approved"
        assert [lease.worker_id for lease in leases] == [
            "worker-before-restart",
            "worker-after-restart",
            "worker-after-restart",
        ]
        assert all(lease.status == "completed" for lease in leases)
        assert state is not None
        assert state["summary"]["status"] == "complete"
        assert state["summary"]["blocked_reasons"] == []


def test_worker_heartbeat_preserves_existing_capacity_routing_fields() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-capacity",
                worker_type="self_hosted",
                status="online",
                queue_name="agent_runs",
                capacity={
                    "max_jobs": 1,
                    "worker_type": "self_hosted",
                    "runtime_modes": ["self_hosted"],
                    "capabilities": ["code.execute"],
                    "memory_mb": 2048,
                },
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()

    runner = WorkerRunner(
        queue=_queue(),
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-capacity",
            worker_type="self_hosted",
            queue_name="agent_runs",
            max_jobs=2,
        ),
    )

    runner.record_heartbeat("online", {"processed": 0})

    with session_factory() as session:
        node = session.scalar(select(WorkerNode).where(WorkerNode.worker_id == "worker-capacity"))
        assert node is not None
        assert node.capacity == {
            "max_jobs": 2,
            "worker_type": "self_hosted",
            "runtime_modes": ["self_hosted"],
            "capabilities": ["code.execute"],
            "memory_mb": 2048,
        }


def test_worker_maintenance_enqueues_team_execution_loop_jobs() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, team_id, user_id, _ = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-team-loop", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()
    duplicate_summary = runner.run_maintenance()
    job = queue.dequeue()

    assert summary.team_execution_loop_jobs_enqueued == 1
    assert duplicate_summary.team_execution_loop_jobs_skipped == 1
    assert duplicate_summary.team_execution_loop_skip_reasons == {"queue_idempotency_duplicate": 1}
    assert job is not None
    assert job.workspace_id == workspace_id
    assert job.resource_id == team_id
    assert job.requested_by_user_id == user_id
    assert job.job_type == JobType.TEAM_EXECUTION_LOOP


def test_worker_maintenance_enqueues_running_team_runtime_without_tasks() -> None:
    session_factory = _session_factory()
    queue = _queue()
    with session_factory() as session:
        user = User(email="runtime-loop@example.com", display_name="Runtime Loop Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(
            owner_user_id=user.id,
            name="Runtime Loop Workspace",
            slug=f"runtime-loop-{uuid4()}",
        )
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"))
        manager = AgentProfile(workspace_id=workspace.id, name="PM", role="project_manager")
        session.add(manager)
        session.flush()
        team = AgentTeam(
            workspace_id=workspace.id,
            name="Always On Team",
            team_type="software",
            manager_agent_profile_id=manager.id,
            default_task_policy={"team_runtime": {"status": "running"}},
        )
        session.add(team)
        session.commit()
        workspace_id = workspace.id
        team_id = team.id
        user_id = user.id
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-running-team-loop", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()
    job = queue.dequeue()

    assert summary.team_execution_loop_jobs_enqueued == 1
    assert job is not None
    assert job.workspace_id == workspace_id
    assert job.resource_id == team_id
    assert job.requested_by_user_id == user_id
    assert job.routing["trigger"] == "running_team_runtime"
    assert job.job_type == JobType.TEAM_EXECUTION_LOOP


def test_runtime_scheduler_scans_do_not_update_another_workspace_team() -> None:
    from backend.app.teams.execution_loop_queue_dispatch import TeamExecutionLoopQueueDispatcher
    from backend.app.teams.execution_loop_runtime_candidates import _team_loop_candidate

    session_factory = _session_factory()
    _, team_id, user_id = _seed_runtime_team(session_factory)
    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        assert team is not None
        original_policy = dict(team.default_task_policy)
        candidate = _team_loop_candidate(
            workspace_id=uuid4(),
            team_id=team.id,
            requested_by_user_id=user_id,
            priority=5,
            trigger="scheduled_team_runtime",
            task_id=None,
        )
        dispatcher = TeamExecutionLoopQueueDispatcher(session)
        dispatcher._record_enqueued_runtime_scan(candidate, datetime.now(UTC), 1)
        dispatcher._record_duplicate_runtime_scan(candidate, datetime.now(UTC), 1)
        session.commit()
        session.refresh(team)
        assert team.default_task_policy == original_policy


def test_worker_maintenance_prioritizes_stale_team_runtime_recovery() -> None:
    session_factory = _session_factory()
    queue = _queue()
    stale_at = datetime.now(UTC) - timedelta(seconds=900)
    workspace_id, team_id, user_id = _seed_runtime_team(
        session_factory,
        runtime_metadata={
            "status": "running",
            "last_heartbeat_at": stale_at.isoformat(),
        },
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-stale-team-loop", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()
    job = queue.dequeue()

    assert summary.team_execution_loop_jobs_enqueued == 1
    assert job is not None
    assert job.workspace_id == workspace_id
    assert job.resource_id == team_id
    assert job.requested_by_user_id == user_id
    assert job.priority == 15
    assert job.routing["trigger"] == "stale_team_runtime"
    assert job.routing["runtime_health"] == "stale"
    assert job.routing["last_heartbeat_at"] == stale_at.isoformat()


def test_worker_maintenance_prioritizes_degraded_bound_team_runtime() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, team_id, user_id = _seed_runtime_team(
        session_factory,
        runtime_status="running",
        runtime_connection_status="offline",
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-degraded-team-loop",
            queue_name="agent_runs",
        ),
    )

    summary = runner.run_maintenance()
    job = queue.dequeue()

    assert summary.team_execution_loop_jobs_enqueued == 1
    assert job is not None
    assert job.workspace_id == workspace_id
    assert job.resource_id == team_id
    assert job.requested_by_user_id == user_id
    assert job.priority == 20
    assert job.routing["trigger"] == "degraded_team_runtime"
    assert job.routing["runtime_health"] == "degraded"
    assert job.routing["workspace_runtime_id"] is not None


def test_worker_maintenance_does_not_recover_paused_team_runtime() -> None:
    session_factory = _session_factory()
    queue = _queue()
    _workspace_id, _team_id, _user_id = _seed_runtime_team(
        session_factory,
        runtime_metadata={"status": "paused"},
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-paused-team-loop", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()

    assert summary.team_execution_loop_jobs_enqueued == 0
    assert summary.team_execution_loop_jobs_skipped == 1
    assert summary.team_execution_loop_skip_reasons == {"team_runtime_paused": 1}
    assert queue.dequeue() is None
    with session_factory() as session:
        team = session.get(AgentTeam, _team_id)
        assert team is not None
        scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]
        assert scan["status"] == "skipped"
        assert scan["reason"] == "team_runtime_paused"


def test_worker_maintenance_does_not_recover_stopped_team_runtime() -> None:
    session_factory = _session_factory()
    queue = _queue()
    _workspace_id, _team_id, _user_id = _seed_runtime_team(
        session_factory,
        runtime_metadata={"status": "stopped"},
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-stopped-team-loop", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()

    assert summary.team_execution_loop_jobs_enqueued == 0
    assert summary.team_execution_loop_jobs_skipped == 1
    assert summary.team_execution_loop_skip_reasons == {"team_runtime_stopped": 1}
    assert queue.dequeue() is None
    with session_factory() as session:
        team = session.get(AgentTeam, _team_id)
        assert team is not None
        scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]
        assert scan["status"] == "skipped"
        assert scan["reason"] == "team_runtime_stopped"


def test_worker_maintenance_enqueues_healthy_team_runtime_on_scheduled_cadence() -> None:
    session_factory = _session_factory()
    queue = _queue()
    now = datetime.now(UTC)
    last_iteration_at = now - timedelta(seconds=20)
    workspace_id, team_id, user_id = _seed_runtime_team(
        session_factory,
        runtime_metadata={
            "status": "running",
            "last_heartbeat_at": last_iteration_at.isoformat(),
            "last_iteration": {
                "iteration": 3,
                "status": "noop",
                "recorded_at": last_iteration_at.isoformat(),
                "summary": {},
            },
            "scheduling_policy": {
                "loop_interval_seconds": 60,
                "priority": 7,
            },
        },
        runtime_status="running",
        runtime_connection_status="online",
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-scheduled-team-loop",
            queue_name="agent_runs",
        ),
    )

    early = runner.run_maintenance()
    assert early.team_execution_loop_jobs_enqueued == 0
    assert early.team_execution_loop_jobs_skipped == 1
    assert early.team_execution_loop_skip_reasons == {"scheduled_team_runtime_not_due": 1}
    assert queue.dequeue() is None
    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        assert team is not None
        scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]
        assert scan["status"] == "skipped"
        assert scan["reason"] == "scheduled_team_runtime_not_due"

    with session_factory() as session:
        service_summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=now + timedelta(seconds=61),
        )
        team = session.get(AgentTeam, team_id)
        assert team is not None
        due_scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]
    job = queue.dequeue()

    assert service_summary.enqueued == 1
    assert service_summary.skipped_reasons == {}
    assert job is not None
    assert job.workspace_id == workspace_id
    assert job.resource_id == team_id
    assert job.requested_by_user_id == user_id
    assert job.priority == 7
    assert job.routing["trigger"] == "scheduled_team_runtime"
    assert job.routing["runtime_health"] == "healthy"
    assert due_scan["status"] == "enqueued"
    assert due_scan["trigger"] == "scheduled_team_runtime"
    assert due_scan["runtime_health"] == "healthy"


def test_worker_maintenance_skips_team_runtime_when_provider_readiness_blocked() -> None:
    session_factory = _session_factory()
    queue = _queue()
    now = datetime.now(UTC)
    workspace_id, team_id, _ = _seed_runtime_team(
        session_factory,
        runtime_metadata={
            "status": "running",
            "last_heartbeat_at": now.isoformat(),
            "last_iteration": {
                "iteration": 1,
                "status": "noop",
                "recorded_at": (now - timedelta(seconds=120)).isoformat(),
                "summary": {},
            },
            "scheduling_policy": {
                "loop_interval_seconds": 60,
                "priority": 7,
            },
        },
        runtime_status="running",
        runtime_connection_status="online",
    )
    with session_factory() as session:
        workspace = session.get(Workspace, workspace_id)
        team = session.get(AgentTeam, team_id)
        assert workspace is not None
        assert team is not None
        credential = ModelProviderCredentialCommandService(
            session,
            SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
        ).create(
            workspace_id=workspace.id,
            created_by_user_id=workspace.owner_user_id,
            name="Blocked Provider",
            provider="openai-compatible",
            api_key="sk-worker-provider-blocked",
            default_model="gpt-4.1-mini",
            base_url="https://provider.example.test/v1",
            is_default=False,
        )
        credential.health_status = "unhealthy"
        manager = session.get(AgentProfile, team.manager_agent_profile_id)
        assert manager is not None
        manager.model = "workspace-default"
        manager.model_provider_credential_id = credential.id
        session.add(
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                accepts_tasks=True,
            )
        )
        session.flush()
        summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=now + timedelta(seconds=121),
        )
        scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]

    assert summary.enqueued == 0
    assert summary.skipped == 1
    assert summary.skipped_reasons == {"provider_readiness_blocked": 1}
    assert queue.dequeue() is None
    assert scan["status"] == "skipped"
    assert scan["reason"] == "provider_readiness_blocked"
    assert scan["runtime_health"] == "provider_blocked"
    assert scan["provider_readiness"] == {
        "status": "blocked",
        "runtime_blocked_member_count": 1,
        "runtime_blocking_reasons": {"model_provider_unhealthy": 1},
    }
    assert "sk-worker-provider-blocked" not in str(scan)
    assert "provider.example.test/v1" not in str(scan)


def test_worker_maintenance_skips_team_runtime_when_provider_budget_exhausted() -> None:
    session_factory = _session_factory()
    queue = _queue()
    now = datetime.now(UTC)
    workspace_id, team_id, _ = _seed_runtime_team(
        session_factory,
        runtime_metadata={
            "status": "running",
            "last_heartbeat_at": now.isoformat(),
            "last_iteration": {
                "iteration": 1,
                "status": "noop",
                "recorded_at": (now - timedelta(seconds=120)).isoformat(),
                "summary": {},
            },
            "scheduling_policy": {
                "loop_interval_seconds": 60,
                "priority": 7,
            },
        },
        runtime_status="running",
        runtime_connection_status="online",
    )
    with session_factory() as session:
        workspace = session.get(Workspace, workspace_id)
        team = session.get(AgentTeam, team_id)
        assert workspace is not None
        assert team is not None
        credential = ModelProviderCredentialCommandService(
            session,
            SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
        ).create(
            workspace_id=workspace.id,
            created_by_user_id=workspace.owner_user_id,
            name="Budget Exhausted Provider",
            provider="openai-compatible",
            api_key="sk-worker-budget-blocked",
            default_model="gpt-4.1-mini",
            base_url="https://budget-provider.example.test/v1",
            is_default=False,
            budget_metadata={"limits": {"calls": 1}, "usage": {"calls": 1}},
        )
        manager = session.get(AgentProfile, team.manager_agent_profile_id)
        assert manager is not None
        manager.model = "workspace-default"
        manager.model_provider_credential_id = credential.id
        session.add(
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                accepts_tasks=True,
            )
        )
        session.flush()
        summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=now + timedelta(seconds=121),
        )
        scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]

    assert summary.enqueued == 0
    assert summary.skipped == 1
    assert summary.skipped_reasons == {"provider_readiness_blocked": 1}
    assert queue.dequeue() is None
    assert scan["status"] == "skipped"
    assert scan["reason"] == "provider_readiness_blocked"
    assert scan["provider_readiness"] == {
        "status": "blocked",
        "runtime_blocked_member_count": 1,
        "runtime_blocking_reasons": {"model_provider_budget_exhausted": 1},
    }
    assert "sk-worker-budget-blocked" not in str(scan)
    assert "budget-provider.example.test/v1" not in str(scan)


def test_worker_maintenance_skips_team_runtime_when_provider_inactive() -> None:
    session_factory = _session_factory()
    queue = _queue()
    now = datetime.now(UTC)
    workspace_id, team_id, _ = _seed_runtime_team(
        session_factory,
        runtime_metadata={
            "status": "running",
            "last_heartbeat_at": now.isoformat(),
            "last_iteration": {
                "iteration": 1,
                "status": "noop",
                "recorded_at": (now - timedelta(seconds=120)).isoformat(),
                "summary": {},
            },
            "scheduling_policy": {
                "loop_interval_seconds": 60,
                "priority": 7,
            },
        },
        runtime_status="running",
        runtime_connection_status="online",
    )
    with session_factory() as session:
        workspace = session.get(Workspace, workspace_id)
        team = session.get(AgentTeam, team_id)
        assert workspace is not None
        assert team is not None
        credential = ModelProviderCredentialCommandService(
            session,
            SecretEncryptionService(secret="unit-test-secret", key_id="test-key"),
        ).create(
            workspace_id=workspace.id,
            created_by_user_id=workspace.owner_user_id,
            name="Inactive Provider",
            provider="openai-compatible",
            api_key="sk-worker-inactive-blocked",
            default_model="gpt-4.1-mini",
            base_url="https://inactive-provider.example.test/v1",
            is_default=False,
        )
        credential.status = "inactive"
        manager = session.get(AgentProfile, team.manager_agent_profile_id)
        assert manager is not None
        manager.model = "workspace-default"
        manager.model_provider_credential_id = credential.id
        session.add(
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=manager.id,
                team_role="project_manager",
                accepts_tasks=True,
            )
        )
        session.flush()
        summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=now + timedelta(seconds=121),
        )
        scan = team.default_task_policy["team_runtime"]["last_scheduler_scan"]

    assert summary.enqueued == 0
    assert summary.skipped == 1
    assert summary.skipped_reasons == {"provider_readiness_blocked": 1}
    assert queue.dequeue() is None
    assert scan["status"] == "skipped"
    assert scan["reason"] == "provider_readiness_blocked"
    assert scan["provider_readiness"] == {
        "status": "blocked",
        "runtime_blocked_member_count": 1,
        "runtime_blocking_reasons": {"model_provider_not_active": 1},
    }
    assert "sk-worker-inactive-blocked" not in str(scan)
    assert "inactive-provider.example.test/v1" not in str(scan)


def test_team_runtime_maintenance_consume_then_reschedules_on_next_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    queue = _queue()
    docker = FakeDockerClient()
    monkeypatch.setattr(
        "backend.app.workers.job_handlers.context.get_docker_runtime_client",
        lambda: docker,
    )
    workspace_id, team_id, user_id, task_id = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    now = datetime.now(UTC)
    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        task = session.get(Task, task_id)
        assert team is not None
        assert task is not None
        task.status = TaskStatus.COMPLETED.value
        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy["team_runtime"])
        runtime_metadata["last_heartbeat_at"] = now.isoformat()
        runtime_metadata["scheduling_policy"] = {
            "loop_interval_seconds": 60,
            "priority": 9,
        }
        policy["team_runtime"] = runtime_metadata
        team.default_task_policy = policy
        session.commit()
        first_summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=now + timedelta(seconds=61),
        )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-team-loop-reschedule",
            queue_name="agent_runs",
        ),
        settings=Settings(
            environment="test",
            runtime_allowed_images=["python:3.12-slim"],
        ),
    )

    first_job = queue.dequeue()
    assert first_summary.enqueued == 1
    assert first_job is not None
    assert first_job.priority == 9
    assert first_job.requested_by_user_id == user_id
    assert first_job.routing["trigger"] == "scheduled_team_runtime"
    queue.enqueue(first_job, force=True)
    assert runner.run_once() is True

    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        assert team is not None
        first_iteration = team.default_task_policy["team_runtime"]["last_iteration"]
        early_summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=datetime.fromisoformat(first_iteration["recorded_at"]) + timedelta(seconds=30),
        )
        due_summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
            now=datetime.fromisoformat(first_iteration["recorded_at"]) + timedelta(seconds=121),
        )
    rescheduled_job = queue.dequeue()

    assert early_summary.enqueued == 0
    assert early_summary.skipped_reasons == {"scheduled_team_runtime_not_due": 1}
    assert due_summary.enqueued == 1
    assert due_summary.skipped_reasons == {}
    assert rescheduled_job is not None
    assert rescheduled_job.workspace_id == workspace_id
    assert rescheduled_job.resource_id == team_id
    assert rescheduled_job.priority == 9
    assert rescheduled_job.routing["trigger"] == "scheduled_team_runtime"


def test_worker_runner_processes_team_execution_loop_job() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, team_id, user_id, task_id = _seed_team_loop_task(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=user_id,
            idempotency_key=f"team.execution_loop:{workspace_id}:{team_id}:test",
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-team-loop-job", queue_name="agent_runs"),
    )

    assert runner.run_once() is True

    with session_factory() as session:
        task = session.get(Task, task_id)
        team = session.get(AgentTeam, team_id)
        assert task is not None
        assert team is not None
        assert task.status == TaskStatus.COMPLETED.value
        assert task.final_output == {
            "summary": "Approved by PM",
            "source": "pm_acceptance_decision",
            "acceptance_message_id": str(
                session.scalar(select(TaskMessage.id).where(TaskMessage.task_id == task_id))
            ),
            "decision": "approved",
        }
        runtime_metadata = team.default_task_policy["team_runtime"]
        assert runtime_metadata["iteration_count"] == 1
        assert runtime_metadata["last_heartbeat_at"]
        assert runtime_metadata["last_iteration"]["status"] == "advanced"
        assert runtime_metadata["last_iteration"]["summary"]["finalized_task_count"] == 1
        assert "last_worker_failure" not in runtime_metadata


def test_worker_runner_records_team_execution_loop_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, team_id, user_id, _ = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )

    def fail_team_loop(self: WorkerJobHandler, job: JobPayload) -> None:
        raise RuntimeError(
            "provider failed api_key=sk-team-loop-secret "
            "base_url=https://runtime.example.test/private"
        )

    monkeypatch.setattr(WorkerJobHandler, "handle", fail_team_loop)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=user_id,
            idempotency_key=f"team.execution_loop:{workspace_id}:{team_id}:failure",
            max_attempts=2,
            routing={"trigger": "scheduled_team_runtime", "api_key": "sk-routing-secret"},
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-team-loop-failure",
            queue_name="agent_runs",
            retry_base_delay_seconds=30,
        ),
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        runner.run_once()

    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        lease = session.scalar(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-team-loop-failure")
        )
        message = session.scalar(
            select(AgentMessage).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id == team_id,
                AgentMessage.message_type == "team.runtime.worker_retrying",
            )
        )
        assert team is not None
        assert lease is not None
        assert lease.status == "retrying"
        runtime_metadata = team.default_task_policy["team_runtime"]
        failure = runtime_metadata["last_worker_failure"]
        assert runtime_metadata["heartbeat_status"] == "retrying"
        assert failure["status"] == "retrying"
        assert failure["worker_id"] == "worker-team-loop-failure"
        assert failure["will_retry"] is True
        assert failure["attempt"] == 0
        assert failure["max_attempts"] == 2
        assert failure["routing"]["api_key"] == "[redacted]"
        serialized = str(runtime_metadata)
        assert "sk-team-loop-secret" not in serialized
        assert "runtime.example.test/private" not in serialized
        assert "sk-routing-secret" not in serialized
        assert "[redacted]" in serialized
        assert message is not None
        assert message.payload["worker_failure"]["error"] == "[redacted]"
        state = TeamRuntimeService(session).get_state(
            workspace_id=workspace_id,
            team_id=team_id,
        )
        assert state is not None
        assert state.runtime_health == "degraded"
        assert queue.count_scheduled_retries(workspace_id=workspace_id) == 1


def test_worker_runner_records_team_execution_loop_missing_actor_failure() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, team_id, _user_id, _ = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=None,
            idempotency_key=f"team.execution_loop:{workspace_id}:{team_id}:missing-actor",
            max_attempts=2,
            routing={"trigger": "scheduled_team_runtime"},
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-team-loop-missing-actor",
            queue_name="agent_runs",
        ),
    )

    with pytest.raises(ValueError, match="requested_by_user_id"):
        runner.run_once()

    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        lease = session.scalar(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-team-loop-missing-actor")
        )
        message = session.scalar(
            select(AgentMessage).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.agent_team_id == team_id,
                AgentMessage.message_type == "team.runtime.worker_retrying",
            )
        )

        assert team is not None
        assert lease is not None
        assert lease.status == "retrying"
        runtime_metadata = team.default_task_policy["team_runtime"]
        assert runtime_metadata["heartbeat_status"] == "retrying"
        failure = runtime_metadata["last_worker_failure"]
        assert failure["status"] == "retrying"
        assert failure["will_retry"] is True
        assert failure["error"] == "Team execution loop jobs require requested_by_user_id"
        assert failure["routing"]["trigger"] == "scheduled_team_runtime"
        assert message is not None
        assert message.payload["worker_failure"]["status"] == "retrying"
        assert queue.count_scheduled_retries(workspace_id=workspace_id) == 1


def test_worker_runner_team_execution_loop_ensures_workspace_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    queue = _queue()
    docker = FakeDockerClient()
    monkeypatch.setattr(
        "backend.app.workers.job_handlers.context.get_docker_runtime_client",
        lambda: docker,
    )
    workspace_id, team_id, user_id, _ = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.TEAM_EXECUTION_LOOP,
            resource_id=team_id,
            requested_by_user_id=user_id,
            idempotency_key=f"team.execution_loop:{workspace_id}:{team_id}:runtime",
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-team-loop-runtime", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            runtime_allowed_images=["python:3.12-slim"],
        ),
    )

    assert runner.run_once() is True

    with session_factory() as session:
        runtime = session.scalar(
            select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
        )
        team = session.get(AgentTeam, team_id)
        assert runtime is not None
        assert runtime.status == "running"
        assert runtime.connection_status == "online"
        assert runtime.capabilities["team_runtime"]["team_id"] == str(team_id)
        assert team is not None
        team_runtime = team.default_task_policy["team_runtime"]
        assert team_runtime["workspace_runtime_id"] == str(runtime.id)
        assert team_runtime["status"] == "running"
    assert len(docker.created_requests) == 1
    assert docker.created_requests[0].image == "python:3.12-slim"
    assert docker.started == ["container-1"]


def test_degraded_team_runtime_maintenance_job_recovers_workspace_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    queue = _queue()
    docker = FakeDockerClient()
    monkeypatch.setattr(
        "backend.app.workers.job_handlers.context.get_docker_runtime_client",
        lambda: docker,
    )
    workspace_id, team_id, user_id, task_id = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        task = session.get(Task, task_id)
        stale_runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            name="Offline Team Runtime",
            status="running",
            connection_status="offline",
            docker_container_id="offline-container",
            limits={},
            network_policy={},
            capabilities={},
        )
        session.add(stale_runtime)
        session.flush()
        assert team is not None
        assert task is not None
        task.status = TaskStatus.COMPLETED.value
        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy["team_runtime"])
        runtime_metadata["workspace_runtime_id"] = str(stale_runtime.id)
        runtime_metadata["last_heartbeat_at"] = datetime.now(UTC).isoformat()
        policy["team_runtime"] = runtime_metadata
        team.default_task_policy = policy
        session.commit()
        summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
            queue=queue,
        )
    job = queue.dequeue()
    assert summary.enqueued == 1
    assert job is not None
    assert job.priority == 20
    assert job.requested_by_user_id == user_id
    assert job.routing["trigger"] == "degraded_team_runtime"
    assert job.routing["runtime_health"] == "degraded"
    queue.enqueue(job, force=True)
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-team-loop-degraded-recovery",
            queue_name="agent_runs",
        ),
        settings=Settings(
            environment="test",
            runtime_allowed_images=["python:3.12-slim"],
        ),
    )

    assert runner.run_once() is True

    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        old_runtime = session.get(WorkspaceRuntime, UUID(str(job.routing["workspace_runtime_id"])))
        assert team is not None
        runtime_metadata = team.default_task_policy["team_runtime"]
        assert runtime_metadata["status"] == "running"
        assert runtime_metadata["heartbeat_status"] in {"advanced", "noop"}
        assert runtime_metadata["workspace_runtime_id"] == job.routing["workspace_runtime_id"]
        assert old_runtime is not None
        assert old_runtime.status == "running"
        assert old_runtime.connection_status == "online"
    assert len(docker.created_requests) == 0
    assert docker.started == ["offline-container"]


def test_worker_runner_team_runtime_soak_keeps_persistent_context_between_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    queue = _queue()
    docker = FakeDockerClient()
    monkeypatch.setattr(
        "backend.app.workers.job_handlers.context.get_docker_runtime_client",
        lambda: docker,
    )
    workspace_id, team_id, user_id, _task_id = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    for iteration in range(2):
        queue.enqueue(
            JobPayload(
                workspace_id=workspace_id,
                job_type=JobType.TEAM_EXECUTION_LOOP,
                resource_id=team_id,
                requested_by_user_id=user_id,
                idempotency_key=(f"team.execution_loop:{workspace_id}:{team_id}:soak:{iteration}"),
                routing={"trigger": "scheduled_team_runtime"},
            )
        )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-team-loop-soak", queue_name="agent_runs"),
        settings=Settings(
            environment="test",
            runtime_allowed_images=["python:3.12-slim"],
        ),
    )

    assert runner.run_once() is True
    assert runner.run_once() is True

    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        runtime = session.scalar(
            select(WorkspaceRuntime).where(WorkspaceRuntime.workspace_id == workspace_id)
        )
        runtime_sessions = session.scalars(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == workspace_id,
                PersistentAgentSession.agent_team_id == team_id,
            )
        ).all()
        runtime_messages = session.scalars(
            select(AgentMessage).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.message_type == "runtime_iteration",
            )
        ).all()

        assert team is not None
        runtime_metadata = team.default_task_policy["team_runtime"]
        assert runtime_metadata["iteration_count"] == 2
        assert runtime_metadata["last_iteration"]["iteration"] == 2
        assert runtime is not None
        assert runtime.capabilities["team_runtime"]["team_id"] == str(team_id)
        assert {item.scope_type for item in runtime_sessions} == {
            "team_runtime",
            "team_agent",
        }
        assert len(runtime_messages) == 2
        assert {message.thread_id for message in runtime_messages} == {
            runtime_messages[0].thread_id
        }
    assert len(docker.created_requests) == 1
    assert docker.started == ["container-1"]


@pytest.mark.team_soak
def test_team_runtime_scheduled_soak_across_thirty_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _session_factory()
    queue = _queue()
    docker = FakeDockerClient()
    monkeypatch.setattr(
        "backend.app.workers.job_handlers.context.get_docker_runtime_client",
        lambda: docker,
    )
    workspace_id, team_id, user_id, task_id = _seed_team_loop_task(
        session_factory,
        with_runtime_template=True,
    )
    start_at = datetime.now(UTC)
    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        task = session.get(Task, task_id)
        assert team is not None
        assert task is not None
        task.status = TaskStatus.COMPLETED.value
        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy["team_runtime"])
        runtime_metadata["last_heartbeat_at"] = start_at.isoformat()
        runtime_metadata["scheduling_policy"] = {
            "loop_interval_seconds": 60,
            "priority": 6,
        }
        policy["team_runtime"] = runtime_metadata
        team.default_task_policy = policy
        session.commit()

    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-team-loop-long-soak",
            queue_name="agent_runs",
        ),
        settings=Settings(
            environment="test",
            runtime_allowed_images=["python:3.12-slim"],
        ),
    )
    for tick in range(1, 7):
        next_due_at = start_at + timedelta(seconds=121 * tick)
        with session_factory() as session:
            team = session.get(AgentTeam, team_id)
            assert team is not None
            policy = dict(team.default_task_policy or {})
            runtime_metadata = dict(policy["team_runtime"])
            if tick > 1:
                runtime_metadata["last_heartbeat_at"] = (
                    next_due_at - timedelta(seconds=30)
                ).isoformat()
                previous_iteration = dict(runtime_metadata["last_iteration"])
                previous_iteration["recorded_at"] = (
                    next_due_at - timedelta(seconds=121)
                ).isoformat()
                runtime_metadata["last_iteration"] = previous_iteration
                policy["team_runtime"] = runtime_metadata
                team.default_task_policy = policy
            else:
                policy["team_runtime"] = runtime_metadata
                team.default_task_policy = policy
            session.commit()
            summary = TeamExecutionLoopQueueService(session).enqueue_active_team_iterations(
                queue=queue,
                now=next_due_at,
            )
        assert summary.enqueued == 1
        assert runner.run_once() is True
        with session_factory() as session:
            team = session.get(AgentTeam, team_id)
            lease = session.scalar(
                select(WorkerLease)
                .where(
                    WorkerLease.worker_id == "worker-team-loop-long-soak",
                    WorkerLease.status == "completed",
                )
                .order_by(WorkerLease.created_at.desc())
            )
            assert team is not None
            assert lease is not None
            assert lease.lease_metadata["routing"]["trigger"] == "scheduled_team_runtime"

    with session_factory() as session:
        team = session.get(AgentTeam, team_id)
        runtime_messages = session.scalars(
            select(AgentMessage).where(
                AgentMessage.workspace_id == workspace_id,
                AgentMessage.message_type == "runtime_iteration",
            )
        ).all()
        runtime_sessions = session.scalars(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == workspace_id,
                PersistentAgentSession.agent_team_id == team_id,
            )
        ).all()
        assert team is not None
        runtime_metadata = team.default_task_policy["team_runtime"]
        assert runtime_metadata["iteration_count"] == 6
        assert runtime_metadata["last_iteration"]["iteration"] == 6
        assert len(runtime_messages) == 6
        assert {message.thread_id for message in runtime_messages} == {
            runtime_messages[0].thread_id
        }
        assert {item.scope_type for item in runtime_sessions} == {
            "team_runtime",
            "team_agent",
        }
    assert len(docker.created_requests) == 1


def test_worker_runner_continues_after_job_failure() -> None:
    session_factory = _session_factory()
    queue = _queue()
    missing_workspace_id, _, user_id = _seed_run(session_factory, slug="missing-run")
    workspace_id, run_id, _ = _seed_run(session_factory, slug="valid-run")
    queue.enqueue(
        JobPayload(
            workspace_id=missing_workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{missing_workspace_id}:bad",
            max_attempts=1,
        )
    )
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-degraded",
            queue_name="agent_runs",
            heartbeat_interval_seconds=0,
            idle_sleep_seconds=0,
        ),
        agent_runner=DeterministicAgentRunner(),
        sleep=lambda _: None,
    )

    summary = runner.run(max_jobs=2)

    assert summary.processed == 1
    assert summary.failed == 1
    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        heartbeat = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "worker-degraded")
        )
        leases = session.scalars(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-degraded")
        ).all()
        assert run is not None
        assert run.status == RunStatus.COMPLETED.value
        assert heartbeat is not None
        assert heartbeat.status == "degraded"
        assert heartbeat.details["failed"] == 1
        assert "workspace mismatch" in str(heartbeat.details["last_error"])
        assert {lease.status for lease in leases} == {"failed", "completed"}
        failed_lease = next(lease for lease in leases if lease.status == "failed")
        assert failed_lease.lease_metadata["last_lifecycle_event"]["type"] == "failed"
        assert "workspace mismatch" in failed_lease.lease_metadata["error"]


def test_worker_runner_schedules_retry_with_error_metadata() -> None:
    session_factory = _session_factory()
    queue = _queue()
    missing_workspace_id, _, user_id = _seed_run(session_factory, slug="retry-delay-missing")
    _, run_id, _ = _seed_run(session_factory, slug="retry-delay-run")
    job = JobPayload(
        workspace_id=missing_workspace_id,
        job_type=JobType.AGENT_RUN,
        resource_id=run_id,
        requested_by_user_id=user_id,
        idempotency_key=f"agent.run:{missing_workspace_id}:retry-delay",
        max_attempts=2,
    )
    queue.enqueue(job)
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-retry-delay",
            queue_name="agent_runs",
            retry_base_delay_seconds=30,
        ),
    )

    with pytest.raises(ValueError, match="workspace mismatch"):
        runner.run_once()

    assert queue.count_queued(workspace_id=missing_workspace_id) == 0
    assert queue.count_scheduled_retries(workspace_id=missing_workspace_id) == 1
    reclaimed = queue.reclaim_due_retries(now=9_999_999_999)
    assert len(reclaimed) == 1
    assert reclaimed[0].attempt == 1
    assert reclaimed[0].last_error is not None
    assert "workspace mismatch" in reclaimed[0].last_error
    assert reclaimed[0].last_error_type == "ValueError"


def test_worker_runner_marks_archive_export_failed_when_settings_missing() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, export_job_id, user_id = _seed_archive_export_job(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.WORKSPACE_ARCHIVE_EXPORT,
            resource_id=export_job_id,
            requested_by_user_id=user_id,
            idempotency_key=f"workspace.archive_export:{workspace_id}:{export_job_id}",
            max_attempts=1,
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-export-missing-settings",
            queue_name="agent_runs",
        ),
        settings=None,
    )

    with pytest.raises(ValueError, match="Worker settings are required"):
        runner.run_once()

    with session_factory() as session:
        export_job = session.get(WorkspaceExportJob, export_job_id)
        lease = session.scalar(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-export-missing-settings")
        )
        assert export_job is not None
        assert export_job.status == WorkspaceExportJobStatus.FAILED.value
        assert export_job.error == "Worker settings are required for workspace archive export"
        assert export_job.completed_at is not None
        assert lease is not None
        assert lease.status == "failed"
        assert queue.count_dead_letters(workspace_id=workspace_id) == 1


def test_worker_runner_drain_prevents_new_job_claims() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    with session_factory() as session:
        node = WorkerNode(
            worker_id="worker-draining",
            worker_type="cloud",
            status="draining",
            queue_name="agent_runs",
            capacity={},
            details={},
            last_seen_at=datetime.now(UTC),
            drain_requested_at=datetime.now(UTC),
        )
        session.add(node)
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-draining", queue_name="agent_runs"),
    )

    assert runner.run_once() is False
    assert queue.count_queued(workspace_id=workspace_id) == 1
    with session_factory() as session:
        assert session.query(WorkerLease).count() == 0


def test_worker_runner_maintenance_status_prevents_new_job_claims() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-maintenance",
                worker_type="cloud",
                status="maintenance",
                queue_name="agent_runs",
                capacity={"max_jobs": 10},
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-maintenance", queue_name="agent_runs"),
    )

    assert runner.run_once() is False
    assert queue.count_queued(workspace_id=workspace_id) == 1
    with session_factory() as session:
        node = session.scalar(
            select(WorkerNode).where(WorkerNode.worker_id == "worker-maintenance")
        )
        assert node is not None
        assert node.status == "maintenance"
        assert session.query(WorkerLease).count() == 0


def test_worker_runner_quarantined_status_prevents_new_job_claims() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-quarantined",
                worker_type="cloud",
                status="quarantined",
                queue_name="agent_runs",
                capacity={"max_jobs": 10},
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-quarantined", queue_name="agent_runs"),
    )

    assert runner.run_once() is False
    assert queue.count_queued(workspace_id=workspace_id) == 1
    with session_factory() as session:
        node = session.scalar(
            select(WorkerNode).where(WorkerNode.worker_id == "worker-quarantined")
        )
        assert node is not None
        assert node.status == "quarantined"
        assert session.query(WorkerLease).count() == 0


def test_worker_heartbeat_does_not_clear_quarantine() -> None:
    session_factory = _session_factory()
    workspace_id, _, _ = _seed_run(session_factory, slug="worker-quarantine")
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-quarantine-heartbeat",
                worker_type="cloud",
                status="quarantined",
                queue_name="agent_runs",
                capacity={"max_jobs": 1},
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()

    with session_factory() as session:
        WorkerHeartbeatOperationsService(session).record_worker_heartbeat(
            workspace_id=workspace_id,
            worker_id="worker-quarantine-heartbeat",
            worker_type="cloud",
            status="online",
            queue_name="agent_runs",
            details={},
            capacity={"max_jobs": 1},
        )

    with session_factory() as session:
        node = session.scalar(
            select(WorkerNode).where(WorkerNode.worker_id == "worker-quarantine-heartbeat")
        )
        assert node is not None
        assert node.status == "quarantined"


def test_worker_runner_capacity_prevents_new_job_claims_when_full() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-full",
                worker_type="cloud",
                status="online",
                queue_name="agent_runs",
                capacity={"max_jobs": 1},
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.add(
            WorkerLease(
                workspace_id=workspace_id,
                worker_id="worker-full",
                queue_name="agent_runs",
                job_id=uuid4(),
                job_type=JobType.AGENT_RUN.value,
                resource_id=run_id,
                status="running",
                attempt=0,
                lease_metadata={},
                started_at=datetime.now(UTC),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-full", queue_name="agent_runs"),
    )

    assert runner.run_once() is False
    assert queue.count_queued(workspace_id=workspace_id) == 1
    with session_factory() as session:
        leases = session.scalars(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-full")
        ).all()
        assert len(leases) == 1
        assert leases[0].status == "running"


def test_worker_runner_capacity_allows_claim_when_slot_available() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-slot",
                worker_type="cloud",
                status="online",
                queue_name="agent_runs",
                capacity={"max_jobs": 2},
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.add(
            WorkerLease(
                workspace_id=workspace_id,
                worker_id="worker-slot",
                queue_name="agent_runs",
                job_id=uuid4(),
                job_type=JobType.AGENT_RUN.value,
                resource_id=uuid4(),
                status="running",
                attempt=0,
                lease_metadata={},
                started_at=datetime.now(UTC),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-slot", queue_name="agent_runs"),
        agent_runner=DeterministicAgentRunner(),
    )

    assert runner.run_once() is True
    assert queue.count_queued(workspace_id=workspace_id) == 0
    with session_factory() as session:
        leases = session.scalars(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-slot")
        ).all()
        assert {lease.status for lease in leases} == {"running", "completed"}


def test_worker_runner_skips_jobs_that_do_not_match_worker_capacity() -> None:
    session_factory = _session_factory()
    queue = _queue()
    docker_workspace_id, docker_run_id, docker_user_id = _seed_run(
        session_factory,
        slug="docker-job",
    )
    systemd_workspace_id, systemd_run_id, systemd_user_id = _seed_run(
        session_factory,
        slug="systemd-job",
    )
    queue.enqueue(
        JobPayload(
            workspace_id=docker_workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=docker_run_id,
            requested_by_user_id=docker_user_id,
            idempotency_key=f"agent.run:{docker_workspace_id}:{docker_run_id}",
            routing={
                "runtime_modes": ["docker"],
                "capabilities": ["image.generate"],
                "resource_requirements": {"memory_mb": 4096},
            },
        )
    )
    queue.enqueue(
        JobPayload(
            workspace_id=systemd_workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=systemd_run_id,
            requested_by_user_id=systemd_user_id,
            idempotency_key=f"agent.run:{systemd_workspace_id}:{systemd_run_id}",
            routing={"runtime_modes": ["systemd"]},
        )
    )
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-systemd",
                worker_type="cloud",
                status="online",
                queue_name="agent_runs",
                capacity={
                    "max_jobs": 1,
                    "worker_type": "cloud",
                    "runtime_modes": ["systemd"],
                    "capabilities": ["code.execute"],
                    "memory_mb": 2048,
                },
                details={},
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-systemd", queue_name="agent_runs"),
        agent_runner=DeterministicAgentRunner(),
    )

    assert runner.run_once() is True
    assert queue.count_queued(workspace_id=docker_workspace_id) == 1
    assert queue.count_queued(workspace_id=systemd_workspace_id) == 0
    with session_factory() as session:
        docker_run = session.get(AgentRun, docker_run_id)
        systemd_run = session.get(AgentRun, systemd_run_id)
        lease = session.scalar(select(WorkerLease).where(WorkerLease.worker_id == "worker-systemd"))
        assert docker_run is not None
        assert docker_run.status == RunStatus.QUEUED.value
        assert systemd_run is not None
        assert systemd_run.status == RunStatus.COMPLETED.value
        assert lease is not None
        assert lease.resource_id == systemd_run_id
        assert lease.lease_metadata["routing"] == {"runtime_modes": ["systemd"]}


def test_worker_runner_processes_mcp_tool_execution_job() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, _ = _seed_run(session_factory)
    with session_factory() as session:
        server = McpServer(
            workspace_id=workspace_id,
            name="image-tools",
            server_type="streamable_http",
            connection={"url": "https://mcp.example.test/jsonrpc"},
        )
        session.add(server)
        session.flush()
        session.add(
            McpToolAllowlist(
                workspace_id=workspace_id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                capability_key="image.generate",
                risk_level="low",
            )
        )
        run = session.get(AgentRun, run_id)
        assert run is not None
        task = session.scalar(select(Task).where(
            Task.workspace_id == workspace_id, Task.id == run.task_id,
        ))
        agent = session.scalar(select(AgentProfile).where(
            AgentProfile.workspace_id == workspace_id, AgentProfile.id == run.agent_profile_id,
        ))
        assert task is not None and agent is not None
        agent.tool_policy = {"allowed_tools": ["generate_image"]}
        agent.runtime_policy = {"mcp": {"timeout_seconds": 15}}
        session.flush()
        run.input = {
            "authorization_snapshot": RunAuthorizationSnapshotService(
                session, RunRequestBuilder(session, Settings(environment="test")),
            ).build_authorization_snapshot(task, None, agent)
        }
        session.commit()
        server_id = server.id
    adapter = RecordingMcpAdapter({"asset_id": "img_123", "status": "created"})
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.MCP_TOOL_EXECUTION,
            resource_id=run_id,
            idempotency_key=f"mcp.tool:{workspace_id}:{run_id}:generate_image",
            routing={
                "mcp_server_id": str(server_id),
                "tool_name": "generate_image",
                "arguments": {"prompt": "mountain"},
                "runtime_allowed_tools": ["generate_image"],
            },
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-mcp", queue_name="agent_runs"),
        mcp_adapter=adapter,
    )

    assert runner.run_once() is True

    with session_factory() as session:
        log = session.scalar(select(McpToolCallLog))
        lease = session.scalar(select(WorkerLease).where(WorkerLease.worker_id == "worker-mcp"))
        assert log is not None
        assert log.workspace_id == workspace_id
        assert log.agent_run_id == run_id
        assert log.status == "completed"
        assert log.response is not None
        assert log.response["result"] == {"asset_id": "img_123", "status": "created"}
        assert lease is not None
        assert lease.status == "completed"
        assert lease.job_type == JobType.MCP_TOOL_EXECUTION.value
    assert adapter.calls == [
        {
            "tool_name": "generate_image",
            "arguments": {"prompt": "mountain"},
            "timeout_seconds": 15,
        }
    ]


def test_worker_runner_processes_memory_index_job() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, _, _ = _seed_run(session_factory, slug="memory-index")
    with session_factory() as session:
        task = session.scalar(select(Task).where(Task.workspace_id == workspace_id))
        assert task is not None
        task.description = "Customer renewal memory index source"
        task_id = task.id
        session.commit()
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.MEMORY_INDEX,
            resource_id=task_id,
            idempotency_key=f"memory.index:{workspace_id}:task:{task_id}",
            routing={"source_type": "task"},
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-memory", queue_name="agent_runs"),
    )

    assert runner.run_once() is True

    with session_factory() as session:
        entry = session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.source_type == "task",
                WorkspaceMemoryEntry.source_id == str(task_id),
                WorkspaceMemoryEntry.entry_type == "indexed_chunk",
                WorkspaceMemoryEntry.status == "active",
            )
        )
        lease = session.scalar(select(WorkerLease).where(WorkerLease.worker_id == "worker-memory"))
        assert entry is not None
        assert "renewal memory index" in entry.content
        assert entry.memory_metadata["indexed"] is True
        assert lease is not None
        assert lease.status == "completed"
        assert lease.job_type == JobType.MEMORY_INDEX.value


def test_worker_runner_rolls_back_failed_session() -> None:
    session_factory = _session_factory()
    queue = _queue()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-fail", queue_name="agent_runs"),
    )

    with pytest.raises(RuntimeError, match="boom"), runner._session_scope() as session:
        session.add(WorkerHeartbeat(worker_id="dirty", queue_name="agent_runs", details={}))
        raise RuntimeError("boom")

    with session_factory() as session:
        heartbeat = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "dirty")
        )
        assert heartbeat is None


def test_worker_runner_maintenance_recovers_stale_runs() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, _ = _seed_run(
        session_factory,
        status=RunStatus.RUNNING,
        task_status=TaskStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(seconds=3_600),
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-maintenance",
            queue_name="agent_runs",
            run_lease_seconds=60,
        ),
    )

    maintenance = runner.run_maintenance()

    assert maintenance.recovered_runs == 1
    assert maintenance.expired_leases == 0
    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        task = session.scalar(select(Task).where(Task.workspace_id == workspace_id))
        assert run is not None
        assert task is not None
        assert run.status == RunStatus.FAILED.value
        assert task.status == TaskStatus.FAILED.value


def test_worker_runner_maintenance_requeues_stale_queued_and_fails_waiting_runtime() -> None:
    session_factory = _session_factory()
    queue = _queue()
    queued_workspace_id, queued_run_id, _ = _seed_run(
        session_factory,
        slug="stale-queued",
        status=RunStatus.QUEUED,
        task_status=TaskStatus.QUEUED,
    )
    waiting_workspace_id, waiting_run_id, _ = _seed_run(
        session_factory,
        slug="stale-waiting-runtime",
        status=RunStatus.WAITING_RUNTIME,
        task_status=TaskStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(seconds=3_600),
    )
    stale_at = datetime.now(UTC) - timedelta(seconds=3_600)
    with session_factory() as session:
        queued_run = session.get(AgentRun, queued_run_id)
        waiting_run = session.get(AgentRun, waiting_run_id)
        assert queued_run is not None
        assert waiting_run is not None
        queued_run.created_at = stale_at
        queued_run.updated_at = stale_at
        waiting_run.created_at = stale_at
        waiting_run.updated_at = stale_at
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-stale-all",
            queue_name="agent_runs",
            run_lease_seconds=60,
        ),
    )

    maintenance = runner.run_maintenance()

    assert maintenance.recovered_runs == 2
    requeued = queue.dequeue()
    assert requeued is not None
    assert requeued.workspace_id == queued_workspace_id
    assert requeued.resource_id == queued_run_id
    with session_factory() as session:
        queued_run = session.get(AgentRun, queued_run_id)
        waiting_run = session.get(AgentRun, waiting_run_id)
        waiting_task = session.scalar(select(Task).where(Task.workspace_id == waiting_workspace_id))
        assert queued_run is not None
        assert waiting_run is not None
        assert waiting_task is not None
        assert queued_run.status == RunStatus.QUEUED.value
        assert waiting_run.status == RunStatus.FAILED.value
        assert waiting_run.error is not None
        assert waiting_run.error["message"] == (
            "Runtime tool result did not arrive before the recovery window expired"
        )
        assert waiting_task.status == TaskStatus.FAILED.value


def test_worker_runner_maintenance_expires_stale_worker_leases() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, _ = _seed_run(session_factory, slug="stale-lease")
    with session_factory() as session:
        session.add(
            WorkerLease(
                workspace_id=workspace_id,
                worker_id="worker-expire",
                queue_name="agent_runs",
                job_id=uuid4(),
                job_type=JobType.AGENT_RUN.value,
                resource_id=run_id,
                status="running",
                attempt=0,
                lease_metadata={},
                started_at=datetime.now(UTC) - timedelta(seconds=3_600),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-expire",
            queue_name="agent_runs",
            run_lease_seconds=60,
        ),
    )

    maintenance = runner.run_maintenance()

    assert maintenance.recovered_runs == 0
    assert maintenance.expired_leases == 1
    with session_factory() as session:
        lease = session.scalar(select(WorkerLease).where(WorkerLease.worker_id == "worker-expire"))
        assert lease is not None
        assert lease.status == "expired"
        assert lease.finished_at is not None
        assert lease.lease_metadata["expired_by"] == "worker_maintenance"
        assert lease.lease_metadata["last_lifecycle_event"]["type"] == "expired"


def test_worker_runner_maintenance_keeps_recently_heartbeat_worker_leases() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, _ = _seed_run(session_factory, slug="fresh-lease-heartbeat")
    with session_factory() as session:
        session.add(
            WorkerLease(
                workspace_id=workspace_id,
                worker_id="worker-fresh-heartbeat",
                queue_name="agent_runs",
                job_id=uuid4(),
                job_type=JobType.AGENT_RUN.value,
                resource_id=run_id,
                status="running",
                attempt=0,
                lease_metadata={},
                started_at=datetime.now(UTC) - timedelta(seconds=3_600),
                last_heartbeat_at=datetime.now(UTC),
            )
        )
        session.commit()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-fresh-heartbeat",
            queue_name="agent_runs",
            run_lease_seconds=60,
        ),
    )

    maintenance = runner.run_maintenance()

    assert maintenance.expired_leases == 0
    with session_factory() as session:
        lease = session.scalar(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-fresh-heartbeat")
        )
        assert lease is not None
        assert lease.status == "running"
        assert lease.finished_at is None


def test_worker_heartbeat_appends_running_lease_lifecycle_event() -> None:
    session_factory = _session_factory()
    workspace_id, run_id, _ = _seed_run(session_factory, slug="lease-heartbeat")
    job_id = uuid4()
    with session_factory() as session:
        session.add(
            WorkerLease(
                workspace_id=workspace_id,
                worker_id="worker-heartbeat",
                queue_name="agent_runs",
                job_id=job_id,
                job_type=JobType.AGENT_RUN.value,
                resource_id=run_id,
                status="running",
                attempt=0,
                lease_metadata={},
                started_at=datetime.now(UTC) - timedelta(seconds=300),
            )
        )
        session.commit()

    with session_factory() as session:
        WorkerHeartbeatOperationsService(session).record_worker_heartbeat(
            workspace_id=workspace_id,
            worker_id="worker-heartbeat",
            worker_type="cloud",
            status="online",
            queue_name="agent_runs",
            details={},
            capacity={"max_jobs": 1},
        )

    with session_factory() as session:
        lease = session.scalar(select(WorkerLease).where(WorkerLease.job_id == job_id))
        assert lease is not None
        assert lease.status == "running"
        assert lease.last_heartbeat_at is not None
        assert lease.last_heartbeat_at > lease.started_at
        assert lease.lease_metadata["last_lifecycle_event"]["type"] == "heartbeat"
        assert lease.lease_metadata["last_lifecycle_event"]["status"] == "online"


def test_worker_runner_maintenance_cleans_stale_runtimes_across_workspaces() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, _, _ = _seed_run(session_factory, slug="stale-runtime-a")
    other_workspace_id, _, _ = _seed_run(session_factory, slug="stale-runtime-b")
    with session_factory() as session:
        runtime_space = RuntimeSpace(
            workspace_id=workspace_id,
            name="Runtime Space",
            scope="workspace",
        )
        session.add(runtime_space)
        session.flush()
        stale_runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space.id,
            name="stale",
            status="running",
            connection_status="online",
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=3_600),
        )
        terminal_runtime = WorkspaceRuntime(
            workspace_id=other_workspace_id,
            name="terminal",
            status="stopped",
            connection_status="offline",
        )
        fresh_runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            name="fresh",
            status="running",
            connection_status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
        session.add_all([stale_runtime, terminal_runtime, fresh_runtime])
        session.commit()
        stale_runtime_id = stale_runtime.id
        terminal_runtime_id = terminal_runtime.id
        runtime_space_id = runtime_space.id
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-runtime-maintenance",
            queue_name="agent_runs",
            run_lease_seconds=60,
        ),
    )

    maintenance = runner.run_maintenance()

    assert maintenance.stale_runtimes == 1
    assert maintenance.deleted_runtime_records == 1
    with session_factory() as session:
        stale_runtime = session.get(WorkspaceRuntime, stale_runtime_id)
        terminal_runtime = session.get(WorkspaceRuntime, terminal_runtime_id)
        space_event = session.scalar(
            select(RuntimeSpaceEvent).where(
                RuntimeSpaceEvent.runtime_space_id == runtime_space_id,
                RuntimeSpaceEvent.event_type == "runtime.marked_offline",
            )
        )
        assert stale_runtime is not None
        assert terminal_runtime is not None
        assert stale_runtime.connection_status == "offline"
        assert terminal_runtime.status == "deleted"
        assert space_event is not None
        assert space_event.event_metadata["source"] == "worker.maintenance"


def test_runtime_cleanup_job_handler_expires_workspace_runtime_and_leases() -> None:
    session_factory = _session_factory()
    workspace_id, run_id, _ = _seed_run(session_factory, slug="runtime-cleanup-job")
    with session_factory() as session:
        runtime_space = RuntimeSpace(
            workspace_id=workspace_id,
            name="Cleanup Space",
            scope="workspace",
            policy={},
            status="active",
        )
        stale_runtime = WorkspaceRuntime(
            workspace_id=workspace_id,
            runtime_space_id=runtime_space.id,
            name="stale-runtime",
            status="running",
            connection_status="online",
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=3_600),
        )
        lease = WorkerLease(
            workspace_id=workspace_id,
            worker_id="worker-runtime-cleanup-job",
            queue_name="agent_runs",
            job_id=uuid4(),
            job_type=JobType.AGENT_RUN.value,
            resource_id=run_id,
            status="running",
            attempt=0,
            lease_metadata={},
            started_at=datetime.now(UTC) - timedelta(seconds=3_600),
            last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=3_600),
        )
        session.add_all([runtime_space, stale_runtime, lease])
        session.commit()
        runtime_id = stale_runtime.id
        lease_id = lease.id

    with session_factory() as session:
        WorkerJobHandler(session).handle(
            JobPayload(
                workspace_id=workspace_id,
                job_type=JobType.RUNTIME_CLEANUP,
                resource_id=workspace_id,
                idempotency_key=f"runtime.cleanup:{workspace_id}",
                routing={
                    "stale_after_seconds": 60,
                    "stale_lease_after_seconds": 60,
                },
            )
        )

    with session_factory() as session:
        runtime = session.get(WorkspaceRuntime, runtime_id)
        lease = session.get(WorkerLease, lease_id)
        assert runtime is not None
        assert lease is not None
        assert runtime.connection_status == "offline"
        assert lease.status == "expired"
        assert lease.finished_at is not None
        assert lease.lease_metadata["last_lifecycle_event"]["type"] == "expired"


def test_worker_runner_summary_includes_maintenance_recovery() -> None:
    session_factory = _session_factory()
    queue = _queue()
    _, run_id, _ = _seed_run(
        session_factory,
        status=RunStatus.RUNNING,
        task_status=TaskStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(seconds=3_600),
        slug="maintenance-loop",
    )
    stop_event = Event()
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-maintenance-summary",
            queue_name="agent_runs",
            heartbeat_interval_seconds=0,
            maintenance_interval_seconds=0,
            run_lease_seconds=60,
            idle_sleep_seconds=0,
        ),
        sleep=lambda _: stop_event.set(),
    )

    summary = runner.run(stop_event=stop_event)

    assert summary.processed == 0
    assert summary.failed == 0
    assert summary.recovered_runs == 1
    assert summary.expired_leases == 0
    assert summary.stale_runtimes == 0
    assert summary.deleted_runtime_records == 0
    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        heartbeat = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "worker-maintenance-summary")
        )
        assert run is not None
        assert run.status == RunStatus.FAILED.value
        assert heartbeat is not None
        assert heartbeat.details["recovered_runs"] == 1
        assert heartbeat.details["expired_leases"] == 0
        assert heartbeat.details["stale_runtimes"] == 0
        assert heartbeat.details["deleted_runtime_records"] == 0


def test_worker_runner_summary_rolls_up_all_maintenance_counts() -> None:
    session_factory = _session_factory()
    stop_event = Event()
    runner = WorkerRunner(
        queue=_queue(),
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-maintenance-counts",
            queue_name="agent_runs",
            heartbeat_interval_seconds=0,
            maintenance_interval_seconds=0,
            idle_sleep_seconds=0,
        ),
        sleep=lambda _: stop_event.set(),
    )
    runner.run_maintenance = lambda: WorkerMaintenanceSummary(  # type: ignore[method-assign]
        recovered_runs=1,
        expired_leases=2,
        expired_tool_approvals=3,
        stale_runtimes=4,
        deleted_runtime_records=5,
        lifecycle_backup_jobs_enqueued=5,
        lifecycle_backup_jobs_skipped=6,
        lifecycle_retention_runs_applied=7,
        lifecycle_retention_runs_skipped=8,
        lifecycle_restore_drills_completed=9,
        lifecycle_restore_drills_skipped=10,
        team_execution_loop_jobs_enqueued=11,
        team_execution_loop_jobs_skipped=12,
        team_execution_loop_skip_reasons={
            "team_runtime_paused": 2,
            "queue_idempotency_duplicate": 10,
        },
        task_events_published=13,
        task_event_publish_failures=14,
        scheduled_job_actions_enqueued=15,
        scheduled_job_actions_recorded=16,
        scheduled_job_actions_skipped=17,
        scheduled_job_actions_enqueued_by_job_type={"model_provider.health_check": 15},
        scheduled_job_actions_recorded_by_job_type={"record_due_action": 16},
        scheduled_job_actions_skipped_by_job_type={"task.plan": 17},
    )

    summary = runner.run(stop_event=stop_event)

    assert summary.processed == 0
    assert summary.failed == 0
    assert summary.recovered_runs == 1
    assert summary.expired_leases == 2
    assert summary.expired_tool_approvals == 3
    assert summary.stale_runtimes == 4
    assert summary.deleted_runtime_records == 5
    assert summary.lifecycle_backup_jobs_enqueued == 5
    assert summary.lifecycle_backup_jobs_skipped == 6
    assert summary.lifecycle_retention_runs_applied == 7
    assert summary.lifecycle_retention_runs_skipped == 8
    assert summary.lifecycle_restore_drills_completed == 9
    assert summary.lifecycle_restore_drills_skipped == 10
    assert summary.team_execution_loop_jobs_enqueued == 11
    assert summary.team_execution_loop_jobs_skipped == 12
    assert summary.team_execution_loop_skip_reasons == {
        "team_runtime_paused": 2,
        "queue_idempotency_duplicate": 10,
    }
    assert summary.task_events_published == 13
    assert summary.task_event_publish_failures == 14
    assert summary.scheduled_job_actions_enqueued == 15
    assert summary.scheduled_job_actions_recorded == 16
    assert summary.scheduled_job_actions_skipped == 17
    assert summary.scheduled_job_actions_enqueued_by_job_type == {"model_provider.health_check": 15}
    assert summary.scheduled_job_actions_recorded_by_job_type == {"record_due_action": 16}
    assert summary.scheduled_job_actions_skipped_by_job_type == {"task.plan": 17}
    with session_factory() as session:
        heartbeat = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "worker-maintenance-counts")
        )
        assert heartbeat is not None
        assert heartbeat.details["recovered_runs"] == 1
        assert heartbeat.details["expired_leases"] == 2
        assert heartbeat.details["expired_tool_approvals"] == 3
        assert heartbeat.details["stale_runtimes"] == 4
        assert heartbeat.details["deleted_runtime_records"] == 5
        assert heartbeat.details["lifecycle_backup_jobs_enqueued"] == 5
        assert heartbeat.details["lifecycle_backup_jobs_skipped"] == 6
        assert heartbeat.details["lifecycle_retention_runs_applied"] == 7
        assert heartbeat.details["lifecycle_retention_runs_skipped"] == 8
        assert heartbeat.details["lifecycle_restore_drills_completed"] == 9
        assert heartbeat.details["lifecycle_restore_drills_skipped"] == 10
        assert heartbeat.details["team_execution_loop_jobs_enqueued"] == 11
        assert heartbeat.details["team_execution_loop_jobs_skipped"] == 12
        assert heartbeat.details["team_execution_loop_skip_reasons"] == {
            "team_runtime_paused": 2,
            "queue_idempotency_duplicate": 10,
        }
        assert heartbeat.details["task_events_published"] == 13
        assert heartbeat.details["task_event_publish_failures"] == 14
        assert heartbeat.details["scheduled_job_actions_enqueued"] == 15
        assert heartbeat.details["scheduled_job_actions_recorded"] == 16
        assert heartbeat.details["scheduled_job_actions_skipped"] == 17
        assert heartbeat.details["scheduled_job_actions_enqueued_by_job_type"] == {
            "model_provider.health_check": 15
        }
        assert heartbeat.details["scheduled_job_actions_recorded_by_job_type"] == {
            "record_due_action": 16
        }
        assert heartbeat.details["scheduled_job_actions_skipped_by_job_type"] == {"task.plan": 17}


def test_worker_maintenance_publishes_task_event_outbox_and_counts_result() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, _ = _seed_run(session_factory, slug="task-event-outbox")
    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        outbox_event = TaskEventOutbox(
            workspace_id=workspace_id,
            task_id=run.task_id,
            event_type="task.message.created",
            payload={"message_id": "message-1", "payload_keys": ["api_key"]},
            status="pending",
            attempts=0,
        )
        session.add(outbox_event)
        session.commit()
        outbox_event_id = outbox_event.id
        task_id = run.task_id
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(
            worker_id="worker-task-event-outbox",
            queue_name="agent_runs",
        ),
    )

    summary = runner.run_maintenance()
    events = RedisTaskEventBus(redis=queue.redis, key_prefix="opsmesh").read(
        workspace_id=workspace_id,
        task_id=task_id,
        after_id="0-0",
    )

    assert summary.task_events_published == 1
    assert summary.task_event_publish_failures == 0
    assert len(events) == 1
    assert events[0].event_type == "task.message.created"
    with session_factory() as session:
        stored = session.get(TaskEventOutbox, outbox_event_id)
        assert stored is not None
        assert stored.status == "published"
        assert stored.published_at is not None
        assert stored.stream_id == events[0].id
        assert stored.last_error is None
        assert events[0].event_id == str(stored.event_id)
        assert events[0].outbox_id == str(stored.id)
        assert events[0].payload == {
            "message_id": "message-1",
            "payload_keys": ["api_key"],
            "event_id": str(stored.event_id),
            "outbox_id": str(stored.id),
        }


def test_agent_run_jobs_include_runtime_space_routing_requirements() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    with session_factory() as session:
        runtime_space = RuntimeSpace(
            workspace_id=workspace_id,
            name="Docker Studio",
            scope="workspace",
            policy={
                "runtime_modes": ["docker"],
                "worker_capabilities": ["image.generate"],
                "worker_types": ["cloud"],
                "resource_requirements": {"memory_mb": 4096, "cpu": "2"},
            },
        )
        session.add(runtime_space)
        session.flush()
        run = session.get(AgentRun, run_id)
        assert run is not None
        run.runtime_space_id = runtime_space.id
        enqueued = RunOrchestrationService(session, queue=queue).enqueue_run(
            run,
            requested_by_user_id=user_id,
        )
        session.commit()

    job = queue.dequeue()

    assert enqueued is True
    assert job is not None
    assert job.routing == {
        "runtime_space_id": str(runtime_space.id),
        "runtime_modes": ["docker"],
        "capabilities": ["image.generate"],
        "worker_types": ["cloud"],
        "resource_requirements": {"memory_mb": 4096, "cpu": 2.0},
    }


def test_worker_job_log_context_is_scoped_to_single_job() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, user_id = _seed_run(session_factory)
    queue.enqueue(
        JobPayload(
            workspace_id=workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=run_id,
            requested_by_user_id=user_id,
            idempotency_key=f"agent.run:{workspace_id}:{run_id}",
        )
    )
    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-context", queue_name="agent_runs"),
        agent_runner=DeterministicAgentRunner(),
    )

    assert current_log_context()["worker_id"] is None

    handled = runner.run_once()

    assert handled is True
    assert current_log_context()["worker_id"] is None
    assert current_log_context()["workspace_id"] is None
    assert current_log_context()["run_id"] is None


def _queue(*, visibility_timeout_seconds: int = 900) -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
        visibility_timeout_seconds=visibility_timeout_seconds,
    )


class RecordingMcpAdapter:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def call(
        self,
        *,
        server: McpServer,
        tool_name: str,
        arguments: dict[str, object],
        credential_refs: list[object],
        timeout_seconds: int,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._response


def _session_factory() -> sessionmaker[Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed_multi_agent_task(
    session_factory: sessionmaker[Session],
) -> tuple[UUID, UUID, UUID]:
    with session_factory() as session:
        user = User(email="restart-team@example.com", display_name="Team Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(
            owner_user_id=user.id,
            name="Restart Team",
            slug="restart-team",
        )
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"))
        credential = ModelProviderCredentialCommandService(
            session,
            SecretEncryptionService(
                secret="change-me-credential-encryption-secret",
                key_id="local",
            ),
        ).create(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            name="Team provider",
            provider="openai",
            api_key="sk-test-multi-agent-restart",
            default_model="gpt-4.1",
            base_url=None,
            is_default=True,
        )
        manager = AgentProfile(
            workspace_id=workspace.id,
            name="Manager",
            role="project_manager",
            instructions="Plan the task and approve the specialist delivery.",
            model="gpt-4.1",
            model_provider_credential_id=credential.id,
        )
        specialist = AgentProfile(
            workspace_id=workspace.id,
            name="Specialist",
            role="developer",
            instructions="Complete the assigned work package.",
            model="gpt-4.1",
            model_provider_credential_id=credential.id,
        )
        session.add_all([manager, specialist])
        session.flush()
        team = AgentTeam(
            workspace_id=workspace.id,
            name="Restart-safe team",
            team_type="software",
            manager_agent_profile_id=manager.id,
        )
        session.add(team)
        session.flush()
        session.add(
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=specialist.id,
                team_role="developer",
                order_index=1,
            )
        )
        task = Task(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            agent_team_id=team.id,
            title="Build restart-safe delivery",
            description="Complete specialist work and obtain manager approval.",
            status=TaskStatus.QUEUED.value,
        )
        session.add(task)
        session.commit()
        return workspace.id, task.id, user.id


def _seed_archive_export_job(
    session_factory: sessionmaker[Session],
) -> tuple[object, object, object]:
    with session_factory() as session:
        user = User(email=f"export-{uuid4()}@example.com", display_name="Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(
            name="Archive Export",
            slug=f"archive-export-{uuid4()}",
            owner_user_id=user.id,
        )
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"))
        export_job = WorkspaceExportJob(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            export_type="workspace_archive",
            status=WorkspaceExportJobStatus.QUEUED.value,
            request={
                "include_agents": True,
                "include_teams": True,
                "include_tasks": True,
                "include_runs": True,
                "include_files": True,
                "include_runtime_spaces": True,
                "include_skill_installs": True,
                "include_audit_events": True,
                "include_file_bytes": True,
                "include_artifact_bytes": True,
                "max_items_per_collection": 500,
                "max_bytes_per_object": 25 * 1024 * 1024,
                "max_total_bytes": 100 * 1024 * 1024,
            },
            job_metadata={},
        )
        session.add(export_job)
        session.commit()
        return workspace.id, export_job.id, user.id


def _seed_run(
    session_factory: sessionmaker[Session],
    *,
    status: RunStatus = RunStatus.QUEUED,
    task_status: TaskStatus = TaskStatus.QUEUED,
    started_at: datetime | None = None,
    slug: str = "workspace",
) -> tuple[object, object, object]:
    with session_factory() as session:
        user = User(email=f"{slug}@example.com", display_name="Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(name=slug.title(), slug=slug, owner_user_id=user.id)
        session.add(workspace)
        session.flush()
        member = WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner")
        task = Task(
            workspace_id=workspace.id,
            title="Do work",
            description="Finish this task",
            status=task_status.value,
            created_by_user_id=user.id,
        )
        agent = AgentProfile(
            workspace_id=workspace.id,
            name="Runner",
            role="worker",
            instructions="Finish tasks.",
            model="gpt-4.1",
        )
        session.add_all([member, task, agent])
        session.flush()
        credential = ModelProviderCredentialCommandService(
            session,
            SecretEncryptionService(
                secret="change-me-credential-encryption-secret",
                key_id="local",
            ),
        ).create(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            name="Default test provider",
            provider="openai",
            api_key="sk-test-worker-runner",
            default_model="gpt-4.1",
            base_url=None,
            is_default=True,
        )
        agent.model_provider_credential_id = credential.id
        run = AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            agent_profile_id=agent.id,
            status=status.value,
            input={
                "task_id": str(task.id),
                "authorization_snapshot": RunAuthorizationSnapshotService(
                    session, RunRequestBuilder(session, Settings(environment="test")),
                ).build_authorization_snapshot(task, None, agent),
            },
            started_at=started_at,
            created_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        return workspace.id, run.id, user.id


def _seed_team_loop_task(
    session_factory: sessionmaker[Session],
    *,
    with_runtime_template: bool = False,
) -> tuple[object, object, object, object]:
    with session_factory() as session:
        user = User(email=f"team-loop-{uuid4()}@example.com", display_name="Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(
            name="Team Loop",
            slug=f"team-loop-{uuid4()}",
            owner_user_id=user.id,
        )
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"))
        manager = AgentProfile(
            workspace_id=workspace.id,
            name="PM",
            role="project_manager",
        )
        developer = AgentProfile(
            workspace_id=workspace.id,
            name="Developer",
            role="developer",
        )
        runtime_template = (
            RuntimeTemplate(
                name=f"team-loop-template-{uuid4()}",
                image="python:3.12-slim",
                default_limits={
                    "cpu_count": 1,
                    "memory_mb": 512,
                    "disk_mb": 1024,
                    "timeout_seconds": 60,
                },
                default_network_policy={"disabled": True},
                created_at=datetime.now(UTC),
            )
            if with_runtime_template
            else None
        )
        session.add_all(
            [item for item in (manager, developer, runtime_template) if item is not None]
        )
        session.flush()
        team = AgentTeam(
            workspace_id=workspace.id,
            name="Loop Team",
            team_type="software",
            manager_agent_profile_id=manager.id,
            default_task_policy={
                "team_runtime": {
                    "status": "running",
                    "runtime_template_id": str(runtime_template.id),
                }
            }
            if runtime_template is not None
            else {},
        )
        session.add(team)
        session.flush()
        session.add_all(
            [
                AgentTeamMember(
                    workspace_id=workspace.id,
                    agent_team_id=team.id,
                    agent_profile_id=manager.id,
                    team_role="project_manager",
                    max_concurrent_tasks=2,
                    order_index=1,
                ),
                AgentTeamMember(
                    workspace_id=workspace.id,
                    agent_team_id=team.id,
                    agent_profile_id=developer.id,
                    team_role="developer",
                    max_concurrent_tasks=2,
                    order_index=2,
                ),
            ]
        )
        task = Task(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            agent_team_id=team.id,
            title="Finalize loop task",
            status=TaskStatus.RUNNING.value,
            priority=8,
            team_snapshot={"team": {"manager_agent_profile_id": str(manager.id)}},
            project_plan={"planner_agent_profile_id": str(manager.id)},
        )
        session.add(task)
        session.flush()
        manager_summary = TaskStep(
            workspace_id=workspace.id,
            task_id=task.id,
            assigned_agent_profile_id=manager.id,
            work_package_id="manager-summary",
            required_role="project_manager",
            title="Review task",
            status="completed",
            order_index=30,
        )
        session.add_all(
            [
                TaskStep(
                    workspace_id=workspace.id,
                    task_id=task.id,
                    assigned_agent_profile_id=manager.id,
                    work_package_id="manager-planning",
                    required_role="project_manager",
                    title="Plan task",
                    status="completed",
                    order_index=10,
                ),
                TaskStep(
                    workspace_id=workspace.id,
                    task_id=task.id,
                    assigned_agent_profile_id=developer.id,
                    work_package_id="build",
                    required_role="developer",
                    title="Build task",
                    status="completed",
                    order_index=20,
                ),
                manager_summary,
            ]
        )
        session.flush()
        session.add(
            TaskMessage(
                workspace_id=workspace.id,
                task_id=task.id,
                task_step_id=manager_summary.id,
                agent_profile_id=manager.id,
                message_type="pm.acceptance_decision",
                sequence=1,
                body="Approved.",
                payload={"decision": "approved", "summary": "Approved by PM"},
            )
        )
        session.commit()
        return workspace.id, team.id, user.id, task.id


def _seed_runtime_team(
    session_factory: sessionmaker[Session],
    *,
    runtime_metadata: dict[str, object] | None = None,
    runtime_status: str | None = None,
    runtime_connection_status: str = "online",
) -> tuple[object, object, object]:
    with session_factory() as session:
        user = User(email=f"runtime-team-{uuid4()}@example.com", display_name="Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(
            name="Runtime Team",
            slug=f"runtime-team-{uuid4()}",
            owner_user_id=user.id,
        )
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="owner"))
        manager = AgentProfile(
            workspace_id=workspace.id,
            name="PM",
            role="project_manager",
        )
        session.add(manager)
        session.flush()
        metadata = dict(runtime_metadata or {"status": "running"})
        if runtime_status is not None:
            runtime = WorkspaceRuntime(
                workspace_id=workspace.id,
                name="Team Runtime",
                status=runtime_status,
                connection_status=runtime_connection_status,
                limits={},
                network_policy={},
                capabilities={},
            )
            session.add(runtime)
            session.flush()
            metadata["workspace_runtime_id"] = str(runtime.id)
        team = AgentTeam(
            workspace_id=workspace.id,
            name="Runtime Team",
            team_type="software",
            manager_agent_profile_id=manager.id,
            default_task_policy={"team_runtime": metadata},
        )
        session.add(team)
        session.commit()
        return workspace.id, team.id, user.id


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
