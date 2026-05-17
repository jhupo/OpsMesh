from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agents.models import AgentProfile
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.operations.models import WorkerHeartbeat
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
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
    assert summary.stopped is False
    with session_factory() as session:
        heartbeat = session.scalar(
            select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == "worker-loop")
        )
        assert heartbeat is not None
        assert heartbeat.status == "online"
        assert heartbeat.details["processed"] == 1


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

    recovered = runner.run_maintenance()

    assert recovered == 1
    with session_factory() as session:
        run = session.get(AgentRun, run_id)
        task = session.scalar(select(Task).where(Task.workspace_id == workspace_id))
        assert run is not None
        assert task is not None
        assert run.status == RunStatus.FAILED.value
        assert task.status == TaskStatus.FAILED.value


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _seed_run(
    session_factory: sessionmaker[Session],
    *,
    status: RunStatus = RunStatus.QUEUED,
    task_status: TaskStatus = TaskStatus.QUEUED,
    started_at: datetime | None = None,
) -> tuple[object, object, object]:
    with session_factory() as session:
        user = User(email="owner@example.com", display_name="Owner")
        session.add(user)
        session.flush()
        workspace = Workspace(name="Workspace", slug="workspace", owner_user_id=user.id)
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
