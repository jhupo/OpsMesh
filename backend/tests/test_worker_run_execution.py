from uuid import uuid4

import fakeredis
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task
from backend.app.tasks.status import TaskStatus
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue, consume_once
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_task_start_creates_queued_run_and_worker_completes_fake_run() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    session.add(task)
    session.flush()

    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    orchestration = RunOrchestrationService(session, queue)
    run = orchestration.create_queued_run_for_task(task)
    enqueued = orchestration.enqueue_run(run, requested_by_user_id=user.id)
    session.commit()

    assert enqueued is True
    assert task.status == TaskStatus.QUEUED.value
    assert run.status == RunStatus.QUEUED.value

    handled = consume_once(queue, WorkerJobHandler(session, queue).handle)

    stored_run = session.get(AgentRun, run.id)
    stored_task = session.get(Task, task.id)
    events = session.scalars(
        select(RunEvent).where(RunEvent.agent_run_id == run.id).order_by(RunEvent.sequence)
    ).all()

    assert handled is True
    assert stored_run is not None
    assert stored_run.status == RunStatus.COMPLETED.value
    assert stored_run.output == {"final_output": "fake_run_completed"}
    assert stored_task is not None
    assert stored_task.status == TaskStatus.COMPLETED.value
    assert [event.event_type for event in events] == ["run.started", "run.completed"]


def test_worker_rejects_workspace_mismatch() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()

    bad_job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="bad",
    )

    try:
        WorkerJobHandler(session).handle(bad_job)
    except ValueError as exc:
        assert "workspace mismatch" in str(exc)
    else:
        raise AssertionError("Expected workspace mismatch to raise")


def test_failed_worker_job_is_retried_by_queue() -> None:
    queue = RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("chaincloud"),
        queue_name="agent_runs",
    )
    job = JobPayload(
        workspace_id=uuid4(),
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key="retry-demo",
        max_attempts=2,
    )
    queue.enqueue(job)

    try:
        consume_once(queue, lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected failed handler to raise")

    retried = queue.dequeue()
    assert retried is not None
    assert retried.attempt == 1


def test_worker_persists_failed_run_event() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Draft report")
    session.add(task)
    session.flush()
    run = RunOrchestrationService(session).create_queued_run_for_task(task)
    session.commit()

    class FailingRunner:
        async def run(self, request):
            raise RuntimeError("model failed")

    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key="fail-run",
    )

    try:
        WorkerJobHandler(session, agent_runner=FailingRunner()).handle(job)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected failing runner to raise")

    stored_run = session.get(AgentRun, run.id)
    failed_event = session.scalar(
        select(RunEvent).where(
            RunEvent.agent_run_id == run.id,
            RunEvent.event_type == "run.failed",
        )
    )

    assert stored_run is not None
    assert stored_run.status == RunStatus.FAILED.value
    assert stored_run.error == {
        "code": "RuntimeError",
        "message": "model failed",
        "retryable": True,
    }
    assert failed_event is not None


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email="owner@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug="acme", settings={})
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
