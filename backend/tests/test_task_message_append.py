from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.tasks.message_append import (
    TASK_MESSAGE_CREATED_EVENT_TYPE,
    TaskMessageAppendService,
)
from backend.app.tasks.models import Task, TaskEventOutbox, TaskMessage
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_task_message_append_assigns_ordered_sequences_and_payloads() -> None:
    session = _session()
    _, workspace, task = _seed_task(session)

    first = TaskMessageAppendService(session).append_for_task(
        task,
        message_type="task.progress.updated",
        body="Started",
        payload={"status": "running"},
    )
    second = TaskMessageAppendService(session).append(
        workspace_id=workspace.id,
        task_id=task.id,
        message_type="task.progress.updated",
        body="Still running",
        payload=None,
    )

    session.commit()

    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task.id).order_by(TaskMessage.sequence)
    ).all()
    assert [message.id for message in messages] == [first.id, second.id]
    assert [message.sequence for message in messages] == [1, 2]
    assert [message.body for message in messages] == ["Started", "Still running"]
    assert messages[0].payload == {"status": "running"}
    assert messages[1].payload == {}


def test_task_message_append_outbox_rolls_back_with_outer_transaction() -> None:
    session = _session()
    _, _, task = _seed_task(session)

    transaction = session.begin()
    task.priority = 1
    session.flush()
    TaskMessageAppendService(session).append_for_task(
        task,
        message_type="agent.progress",
        body="This transaction will roll back",
        payload={"progress": "drafting"},
    )
    assert (
        session.scalar(select(TaskEventOutbox).where(TaskEventOutbox.task_id == task.id))
        is not None
    )

    transaction.rollback()

    assert session.scalar(select(TaskEventOutbox).where(TaskEventOutbox.task_id == task.id)) is None
    assert session.scalar(select(TaskMessage).where(TaskMessage.task_id == task.id)) is None


def test_task_message_append_retries_sequence_collision_in_savepoint() -> None:
    session = _session()
    _, _, task = _seed_task(session)
    session.add(
        TaskMessage(
            workspace_id=task.workspace_id,
            task_id=task.id,
            message_type="existing",
            sequence=1,
            body="Existing",
            payload={},
        )
    )
    session.commit()

    service = TaskMessageAppendService(session)
    attempts: list[int] = []

    def colliding_then_next(workspace_id: UUID, task_id: UUID) -> int:
        attempts.append(1)
        return 1 if len(attempts) == 1 else 2

    service._next_sequence = colliding_then_next  # type: ignore[method-assign]

    message = service.append_for_task(
        task,
        message_type="task.progress.updated",
        body="Recovered",
        payload={"attempt": "retried"},
    )
    session.commit()

    messages = session.scalars(
        select(TaskMessage).where(TaskMessage.task_id == task.id).order_by(TaskMessage.sequence)
    ).all()
    assert len(attempts) == 2
    assert message.sequence == 2
    assert [stored.sequence for stored in messages] == [1, 2]
    assert messages[-1].payload == {"attempt": "retried"}


def test_task_message_append_commits_message_created_outbox_event() -> None:
    session = _session()
    _, _, task = _seed_task(session)

    message = TaskMessageAppendService(session).append_for_task(
        task,
        message_type="agent.progress",
        body="Secret body must stay in the DB only",
        payload={"api_key": "sk-hidden", "progress": "drafting"},
    )
    session.commit()

    outbox_event = session.scalar(
        select(TaskEventOutbox).where(TaskEventOutbox.task_id == task.id)
    )
    assert outbox_event is not None
    assert outbox_event.workspace_id == task.workspace_id
    assert outbox_event.task_id == task.id
    assert outbox_event.event_type == TASK_MESSAGE_CREATED_EVENT_TYPE
    assert outbox_event.status == "pending"
    assert outbox_event.attempts == 0
    assert outbox_event.available_at is not None
    assert outbox_event.published_at is None
    assert outbox_event.last_error is None
    event_id = str(outbox_event.event_id)
    outbox_id = str(outbox_event.id)
    assert outbox_event.payload == {
        "message_id": str(message.id),
        "message_type": "agent.progress",
        "sequence": 1,
        "task_step_id": None,
        "agent_run_id": None,
        "agent_profile_id": None,
        "has_body": True,
        "payload_keys": ["api_key", "progress"],
        "created_at": message.created_at.isoformat(),
        "event_id": event_id,
        "outbox_id": outbox_id,
    }
    assert "Secret body" not in str(outbox_event.payload)
    assert "sk-hidden" not in str(outbox_event.payload)


def test_task_message_append_persists_message_and_outbox_event_only() -> None:
    session = _session()
    _, _, task = _seed_task(session)

    message = TaskMessageAppendService(session).append_for_task(
        task,
        message_type="agent.progress",
        body="Persist only",
        payload={"progress": "drafting"},
    )
    session.commit()

    stored = session.scalar(select(TaskMessage).where(TaskMessage.id == message.id))
    assert stored is not None
    assert stored.sequence == 1
    assert session.scalar(
        select(TaskEventOutbox).where(TaskEventOutbox.task_id == task.id)
    ) is not None


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_task(session: Session) -> tuple[User, Workspace, Task]:
    user = User(email="owner@example.test", display_name="Owner")
    session.add(user)
    session.flush()
    workspace = Workspace(owner_user_id=user.id, name="Workspace", slug="workspace")
    session.add(workspace)
    session.flush()
    membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    session.add(membership)
    session.flush()
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Task")
    session.add(task)
    session.commit()
    return user, workspace, task


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
