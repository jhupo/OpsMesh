from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import fakeredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.capabilities.models import McpServer, McpToolAllowlist, McpToolCallLog
from backend.app.core.request_context import current_log_context
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.operations.service import OperationsService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceEvent
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue
from backend.app.workers.runner import WorkerRunner, WorkerRunnerConfig
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_worker_runner_run_once_processes_agent_job() -> None:
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
        config=WorkerRunnerConfig(worker_id="worker-1", queue_name="agent_runs"),
    )

    assert runner.run_once() is True

    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED.value
        assert run.output is not None
        assert "final_output" in run.output


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
        OperationsService(session).record_worker_heartbeat(
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
    self_hosted_workspace_id, self_hosted_run_id, self_hosted_user_id = _seed_run(
        session_factory,
        slug="self-hosted-job",
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
            workspace_id=self_hosted_workspace_id,
            job_type=JobType.AGENT_RUN,
            resource_id=self_hosted_run_id,
            requested_by_user_id=self_hosted_user_id,
            idempotency_key=f"agent.run:{self_hosted_workspace_id}:{self_hosted_run_id}",
            routing={"runtime_modes": ["self_hosted"]},
        )
    )
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="worker-self-hosted",
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
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-self-hosted", queue_name="agent_runs"),
    )

    assert runner.run_once() is True
    assert queue.count_queued(workspace_id=docker_workspace_id) == 1
    assert queue.count_queued(workspace_id=self_hosted_workspace_id) == 0
    with session_factory() as session:
        docker_run = session.get(AgentRun, docker_run_id)
        self_hosted_run = session.get(AgentRun, self_hosted_run_id)
        lease = session.scalar(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-self-hosted")
        )
        assert docker_run is not None
        assert docker_run.status == RunStatus.QUEUED.value
        assert self_hosted_run is not None
        assert self_hosted_run.status == RunStatus.COMPLETED.value
        assert lease is not None
        assert lease.resource_id == self_hosted_run_id
        assert lease.lease_metadata["routing"] == {"runtime_modes": ["self_hosted"]}


def test_worker_runner_processes_mcp_tool_execution_job() -> None:
    session_factory = _session_factory()
    queue = _queue()
    workspace_id, run_id, _ = _seed_run(session_factory)
    with session_factory() as session:
        server = McpServer(
            workspace_id=workspace_id,
            name="image-tools",
            server_type="http",
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
        run.input = {
            "authorization_snapshot": {
                "version": 1,
                "workspace_id": str(workspace_id),
                "agent_run_id": str(run_id),
                "allowed_tools": ["generate_image"],
                "runtime_policy": {
                    "mcp": {
                        "timeout_seconds": 15,
                        "max_input_bytes": 64_000,
                        "max_output_bytes": 256_000,
                    }
                },
            }
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
        lease = session.scalar(
            select(WorkerLease).where(WorkerLease.worker_id == "worker-memory")
        )
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
        waiting_task = session.scalar(
            select(Task).where(Task.workspace_id == waiting_workspace_id)
        )
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
                started_at=datetime.now(UTC),
            )
        )
        session.commit()

    with session_factory() as session:
        OperationsService(session).record_worker_heartbeat(
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
            select(WorkerHeartbeat).where(
                WorkerHeartbeat.worker_id == "worker-maintenance-summary"
            )
        )
        assert run is not None
        assert run.status == RunStatus.FAILED.value
        assert heartbeat is not None
        assert heartbeat.details["recovered_runs"] == 1
        assert heartbeat.details["expired_leases"] == 0
        assert heartbeat.details["stale_runtimes"] == 0
        assert heartbeat.details["deleted_runtime_records"] == 0


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
    )

    assert current_log_context()["worker_id"] is None

    handled = runner.run_once()

    assert handled is True
    assert current_log_context()["worker_id"] is None
    assert current_log_context()["workspace_id"] is None
    assert current_log_context()["run_id"] is None


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )


class RecordingMcpAdapter:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def call(
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
        run = AgentRun(
            workspace_id=workspace.id,
            task_id=task.id,
            agent_profile_id=agent.id,
            status=status.value,
            input={"task_id": str(task.id)},
            started_at=started_at,
            created_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        return workspace.id, run.id, user.id


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
