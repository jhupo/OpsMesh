from __future__ import annotations

from uuid import UUID

import fakeredis
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.tasks.event_outbox import TaskEventOutboxPublisher, TaskEventOutboxService
from backend.app.tasks.events import RedisTaskEventBus, TaskEvent
from backend.app.tasks.models import Task, TaskEventOutbox
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workers.runner import WorkerRunner, WorkerRunnerConfig
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_task_event_outbox_publisher_publishes_pending_event() -> None:
    session = _session()
    _, workspace, task = _seed_task(session)
    outbox_event = TaskEventOutboxService(session).enqueue(
        workspace_id=workspace.id,
        task_id=task.id,
        event_type="task.message.created",
        payload={"message_id": "message-1"},
    )
    session.commit()
    bus = InMemoryTaskEventBus()

    summary = TaskEventOutboxPublisher(session, bus).publish_pending(limit=10)

    assert summary.scanned == 1
    assert summary.published == 1
    assert summary.failed == 0
    stored = session.get(TaskEventOutbox, outbox_event.id)
    assert stored is not None
    assert stored.status == "published"
    assert stored.published_at is not None
    assert stored.stream_id is not None
    assert stored.attempts == 0
    assert stored.last_error is None
    events = bus.read(workspace_id=workspace.id, task_id=task.id, after_id="0-0")
    assert [event.event_type for event in events] == ["task.message.created"]
    assert events[0].id == stored.stream_id
    assert events[0].event_id == str(stored.event_id)
    assert events[0].outbox_id == str(outbox_event.id)
    assert events[0].payload == {
        "message_id": "message-1",
        "event_id": str(stored.event_id),
        "outbox_id": str(outbox_event.id),
    }


def test_task_event_outbox_publisher_records_failure_and_retries_later() -> None:
    session = _session()
    _, workspace, task = _seed_task(session)
    outbox_event = TaskEventOutboxService(session).enqueue(
        workspace_id=workspace.id,
        task_id=task.id,
        event_type="task.progress",
        payload={"step": "draft"},
    )
    session.commit()

    failed = TaskEventOutboxPublisher(session, _FailingTaskEventBus()).publish_pending(limit=10)

    assert failed.scanned == 1
    assert failed.published == 0
    assert failed.failed == 1
    stored = session.get(TaskEventOutbox, outbox_event.id)
    assert stored is not None
    assert stored.status == "pending"
    assert stored.attempts == 1
    assert stored.published_at is None
    assert stored.stream_id is None
    assert "redis unavailable" in str(stored.last_error)
    event_id = stored.event_id

    bus = InMemoryTaskEventBus()
    retried = TaskEventOutboxPublisher(session, bus).publish_pending(limit=10)

    assert retried.published == 1
    session.refresh(stored)
    assert stored.status == "published"
    assert stored.attempts == 1
    assert stored.event_id == event_id
    assert stored.stream_id is not None
    assert stored.last_error is None
    events = bus.read(workspace_id=workspace.id, task_id=task.id, after_id="0-0")
    assert events[0].payload["event_id"] == str(event_id)
    assert events[0].payload["outbox_id"] == str(stored.id)


def test_worker_maintenance_publishes_task_event_outbox() -> None:
    session_factory = _session_factory()
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
    )
    with session_factory() as session:
        _, workspace, task = _seed_task(session)
        TaskEventOutboxService(session).enqueue(
            workspace_id=workspace.id,
            task_id=task.id,
            event_type="task.maintenance",
            payload={"source": "test"},
        )
        session.commit()
        workspace_id = workspace.id
        task_id = task.id

    runner = WorkerRunner(
        queue=queue,
        session_factory=session_factory,
        config=WorkerRunnerConfig(worker_id="worker-events", queue_name="agent_runs"),
    )

    summary = runner.run_maintenance()

    assert summary.task_events_published == 1
    assert summary.task_event_publish_failures == 0
    with session_factory() as session:
        stored = session.scalar(select(TaskEventOutbox).where(TaskEventOutbox.task_id == task_id))
        assert stored is not None
        assert stored.status == "published"
        assert stored.stream_id is not None
        event_id = str(stored.event_id)
        outbox_id = str(stored.id)
    events = RedisTaskEventBus(redis=redis, key_prefix="opsmesh").read(
        workspace_id=workspace_id,
        task_id=task_id,
        after_id="0-0",
    )
    assert [event.event_type for event in events] == ["task.maintenance"]
    assert events[0].event_id == event_id
    assert events[0].outbox_id == outbox_id
    assert events[0].payload == {
        "source": "test",
        "event_id": event_id,
        "outbox_id": outbox_id,
    }


def _session() -> Session:
    return _session_factory()()


def _session_factory() -> sessionmaker[Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_task(session: Session) -> tuple[User, Workspace, Task]:
    user = User(email=f"owner-{id(session)}@example.test", display_name="Owner")
    session.add(user)
    session.flush()
    workspace = Workspace(owner_user_id=user.id, name="Workspace", slug=f"workspace-{id(user)}")
    session.add(workspace)
    session.flush()
    membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    session.add(membership)
    session.flush()
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add(task)
    session.flush()
    return user, workspace, task


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()


class _FailingTaskEventBus:
    def publish(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        outbox_id: str | None = None,
    ) -> str:
        raise ConnectionError("redis unavailable")

    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
        block_ms: int = 0,
    ) -> list[object]:
        return []


class InMemoryTaskEventBus:
    def __init__(self) -> None:
        self._events: dict[tuple[UUID, UUID], list[TaskEvent]] = {}
        self._sequence = 0

    def publish(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object] | None = None,
        event_id: str | None = None,
        outbox_id: str | None = None,
    ) -> str:
        self._sequence += 1
        stream_id = f"1-{self._sequence}"
        event_payload = dict(payload or {})
        if event_id is not None:
            event_payload["event_id"] = event_id
        if outbox_id is not None:
            event_payload["outbox_id"] = outbox_id
        self._events.setdefault((workspace_id, task_id), []).append(
            TaskEvent(
                id=stream_id,
                workspace_id=workspace_id,
                task_id=task_id,
                event_type=event_type,
                payload=event_payload,
                event_id=event_id,
                outbox_id=outbox_id,
            )
        )
        return stream_id

    def read(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        after_id: str,
        count: int = 10,
        block_ms: int = 0,
    ) -> list[TaskEvent]:
        if after_id == "$":
            return []
        events = self._events.get((workspace_id, task_id), [])
        return [event for event in events if event.id > after_id][:count]
