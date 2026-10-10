import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.bootstrap.worker import build_worker_runner
from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.orchestration.conversations.models import (
    Conversation,
    ConversationEvent,
    ConversationExecution,
    ConversationTurn,
)
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.queues.contracts import JobType
from opsmesh.runtime.queues.dispatch import QueueDispatchPublisher
from opsmesh.runtime.queues.models import QueueDispatch
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.runtime.workers.models import WorkerRunnerConfig
from opsmesh.shared.concurrency import BlockingIO
from opsmesh.shared.db.base import Base
from opsmesh.shared.redis.keys import RedisKeyBuilder
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL URL required"
)


def _seed_conversation(factory):
    with factory() as session:
        user = User(email=f"{uuid4()}@example.com", display_name="Owner")
        workspace = Workspace(owner=user, name="Test", slug=uuid4().hex, settings={})
        member = WorkspaceMember(workspace=workspace, user=user, role="owner")
        session.add_all([user, workspace, member])
        session.flush()
        agent = AgentProfile(
            workspace_id=workspace.id, name="Assistant", role="assistant", instructions="Help"
        )
        session.add(agent)
        session.flush()
        conversation = Conversation(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            title="Test",
            mode="agent",
            agent_profile_id=agent.id,
        )
        session.add(conversation)
        session.commit()
        return workspace.id, user.id, member.id, conversation.id


def _context(session, wid, uid, mid):
    return WorkspaceContext(
        AuthenticatedUser.from_model(session.get(User, uid)),
        session.get(Workspace, wid),
        session.get(WorkspaceMember, mid),
    )


def _queue():
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("test"),
        queue_name="jobs",
        blocking_timeout_seconds=0,
    )


def test_concurrent_message_acceptance_and_transactional_dispatch() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid = _seed_conversation(factory)
        barrier = threading.Barrier(2)
        queue = _queue()

        def send() -> object:
            with factory() as session:
                context = _context(session, wid, uid, mid)
                barrier.wait(timeout=10)
                return (
                    ConversationService(session)
                    .send(context, cid, "hello", "same-request", queue=queue)
                    .id
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = list(executor.map(lambda _: send(), range(2)))
        assert first == second
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1
            assert session.scalar(select(func.count()).select_from(ConversationEvent)) == 1
            ConversationAdvanceService(session).advance(wid, cid)
            session.rollback()
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 0
        barrier = threading.Barrier(2)

        def dispatch() -> bool:
            with factory() as session:
                barrier.wait(timeout=10)
                ConversationAdvanceService(session).advance(wid, cid)
                session.commit()
                return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: dispatch(), range(2)))
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 1
            assert session.scalar(select(ConversationTurn.status)) == "running"
            assert session.scalar(select(func.count()).select_from(QueueDispatch)) >= 2


def test_committed_chat_recovers_redis_failure_and_parallel_publishers(monkeypatch) -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid = _seed_conversation(factory)
        queue = _queue()

        def unavailable(*args, **kwargs):
            raise ConnectionError("Redis unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(RedisQueue, "enqueue", unavailable)
            with factory() as session:
                turn = ConversationService(session).send(
                    _context(session, wid, uid, mid), cid, "hello", "request", queue=queue
                )
                turn_id = turn.id
        with factory() as session:
            assert session.get(ConversationTurn, turn_id).status == "queued"
            dispatch = session.scalar(select(QueueDispatch))
            assert dispatch.attempts == 1
            assert dispatch.published_at is None
            dispatch.available_at = datetime.now(UTC)
            session.commit()
        barrier = threading.Barrier(2)

        def publish():
            with factory() as session:
                barrier.wait(timeout=10)
                return QueueDispatchPublisher(session, queue).publish_pending()

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sum(executor.map(lambda _: publish(), range(2))) == 1
        lease = queue.dequeue_with_lease()
        assert lease.job.job_type == JobType.CONVERSATION_ADVANCE
        assert lease.job.resource_id == cid
        assert queue.dequeue_with_lease() is None
        with factory() as session:
            ConversationAdvanceService(session).advance(wid, cid)
            session.commit()
            before = session.scalar(select(func.count()).select_from(QueueDispatch))
            task = session.scalar(select(Task))
            task.status = "completed"
            session.flush()
            session.rollback()
        with factory() as session:
            assert session.scalar(select(Task.status)) != "completed"
            assert session.scalar(select(func.count()).select_from(QueueDispatch)) == before


def test_chat_turns_complete_in_order_while_maintenance_is_blocked() -> None:
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        wid, uid, mid, cid = _seed_conversation(factory)
        queue = _queue()
        with factory() as session:
            service = ConversationService(session)
            context = _context(session, wid, uid, mid)
            first = service.send(context, cid, "first", "first", queue=queue).id
            second = service.send(context, cid, "second", "second", queue=queue).id
        completed = []
        release_maintenance = threading.Event()
        maintenance_released = []

        async def complete(job, io: BlockingIO, controls: BlockingIO):
            # Replace only the external execution boundary; exercise actual queue leases,
            # task transactions, wakeups and conversation advancement below.
            def persist():
                with factory() as session:
                    run = session.get(AgentRun, job.resource_id)
                    task = session.get(Task, run.task_id)
                    completed.append(task.description)
                    task.final_output = {"text": task.description}
                    run.status = task.status = "completed"
                    session.commit()
                    if len(completed) == 2:
                        release_maintenance.set()

            await io.run(persist)

        def blocked_maintenance():
            from opsmesh.runtime.workers.maintenance_contracts import WorkerMaintenanceSummary

            maintenance_released.append(release_maintenance.wait(10))
            return WorkerMaintenanceSummary(recovered_runs=0, expired_leases=0)

        runner = build_worker_runner(
            queue=queue,
            session_factory=factory,
            config=WorkerRunnerConfig(worker_id="chat-test", idle_sleep_seconds=0.01),
        )
        runner._maintenance = blocked_maintenance
        runner._async_handlers[JobType.AGENT_RUN] = complete
        result = runner.run(max_jobs=6)
        assert result.failed == 0
        assert release_maintenance.is_set()
        assert maintenance_released == [True], "Chat waited for maintenance"
        with factory() as session:
            assert session.get(ConversationTurn, first).reply == "first"
            assert session.get(ConversationTurn, second).reply == "second"
            assert session.get(ConversationTurn, second).status == "completed"
            assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 2
            assert completed == ["first", "second"]
