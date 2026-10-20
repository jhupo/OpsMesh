"""Real PostgreSQL atomic native-history publication for FIFO Runs."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from opsmesh.orchestration.sessions import history
from opsmesh.orchestration.sessions.models import SessionRecord


def create_session(sessions):
    owner_id = uuid4()
    with sessions() as session, session.begin():
        record = SessionRecord(
            scope_id=owner_id,
            principal_id=owner_id,
            owner_ref=f"user:{owner_id}",
            history_revision=0,
        )
        session.add(record)
        session.flush()
        return record.id


def locked(session, session_id):
    return session.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id).with_for_update()
    )


def prepare(sessions, session_id, run_id, content):
    with sessions() as session, session.begin():
        record = locked(session, session_id)
        history.prepare_run(session, model_session=record, run_id=run_id)
        batch = history.run_batch(session, session_id=session_id, run_id=run_id)
        history.append_items(session, batch=batch, items=[{"role": "user", "content": content}])


def publish(sessions, session_id, run_id):
    with sessions() as session, session.begin():
        return history.commit_run(session, model_session=locked(session, session_id), run_id=run_id)


def test_next_run_reads_preceding_committed_history_and_its_own_items(database_sessions):
    session_id, first, second = create_session(database_sessions), uuid4(), uuid4()
    prepare(database_sessions, session_id, first, "A")
    assert publish(database_sessions, session_id, first) == 1
    prepare(database_sessions, session_id, second, "B")
    with database_sessions() as session, session.begin():
        record = locked(session, session_id)
        history.prepare_run(session, model_session=record, run_id=second)
        assert history.read_items(session, session_id=session_id, run_id=second) == [
            {"role": "user", "content": "A"},
            {"role": "user", "content": "B"},
        ]
        assert history.read_items(session, session_id=session_id) == [
            {"role": "user", "content": "A"}
        ]
    assert publish(database_sessions, session_id, second) == 2
    with database_sessions() as session:
        assert history.read_items(session, session_id=session_id) == [
            {"role": "user", "content": "A"},
            {"role": "user", "content": "B"},
        ]


def test_batch_publication_replays_without_duplicate_items_or_revision(database_sessions):
    session_id, run_id = create_session(database_sessions), uuid4()
    prepare(database_sessions, session_id, run_id, "once")
    assert publish(database_sessions, session_id, run_id) == 1
    assert publish(database_sessions, session_id, run_id) == 1
    with database_sessions() as session:
        assert len(history.read_items(session, session_id=session_id)) == 1
        assert session.get(SessionRecord, session_id).history_revision == 1


def test_failed_finalization_rolls_back_publication_but_keeps_private_items(database_sessions):
    session_id, run_id = create_session(database_sessions), uuid4()
    prepare(database_sessions, session_id, run_id, "pending")
    with pytest.raises(RuntimeError), database_sessions() as session, session.begin():
        history.commit_run(session, model_session=locked(session, session_id), run_id=run_id)
        raise RuntimeError("Simulated failure before committing final Run state")
    with database_sessions() as session:
        assert history.read_items(session, session_id=session_id) == []
        assert history.read_items(session, session_id=session_id, run_id=run_id) == [
            {"role": "user", "content": "pending"}
        ]
        assert session.get(SessionRecord, session_id).history_revision == 0
    assert publish(database_sessions, session_id, run_id) == 1


def test_run_pop_cannot_remove_committed_items(database_sessions):
    session_id, first, second = create_session(database_sessions), uuid4(), uuid4()
    prepare(database_sessions, session_id, first, "committed")
    publish(database_sessions, session_id, first)
    prepare(database_sessions, session_id, second, "private")
    with database_sessions() as session, session.begin():
        locked(session, session_id)
        batch = history.run_batch(session, session_id=session_id, run_id=second)
        assert history.pop_item(session, session_id=session_id, batch=batch)["content"] == "private"
        assert history.pop_item(session, session_id=session_id, batch=batch) is None
        assert history.read_items(session, session_id=session_id, run_id=second) == [
            {"role": "user", "content": "committed"}
        ]
