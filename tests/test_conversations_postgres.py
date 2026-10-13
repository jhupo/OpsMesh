"""Concurrent Chat admission and native Temporal delivery without maintenance scans."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from agents import Model
from agents.items import TResponseInputItem
from pydantic import TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.openai_agents.testing import TestModel, TestModelProvider
from temporalio.worker import Worker

from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.orchestration.conversations.models import (
    ConversationEvent,
    ConversationExecution,
    ConversationTurn,
)
from opsmesh.orchestration.conversations.schemas import ConversationCreateRequest
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.conversations.workflow import ConversationWorkflow
from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import DispatchRequest, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import TemporalOutboxPublisher
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.native_agent_worker import (
    OfflineModelProvider,
    answer,
    native_agent_worker,
    stream_completed,
    stream_delta,
    stream_item,
)

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


def _context(session, state):
    return WorkspaceContext(
        AuthenticatedUser.from_model(session.get(User, state.user_id)),
        session.get(Workspace, state.request.workspace_id),
        session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == state.request.workspace_id,
                WorkspaceMember.user_id == state.user_id,
            )
        ),
    )


def _conversation(state):
    with state.sessions() as session:
        session.execute(
            delete(TemporalOutboxEntry).where(
                TemporalOutboxEntry.workflow_id == state.request.workflow_id
            )
        )
        session.commit()
        return (
            ConversationService(session)
            .create(
                _context(session, state),
                ConversationCreateRequest(mode="agent", agent_profile_id=state.profile_id),
            )
            .id
        )


def test_concurrent_message_acceptance_and_transactional_dispatch(native_execution):
    state = native_execution
    conversation = _conversation(state)
    barrier = threading.Barrier(2)

    def send():
        with state.sessions() as session:
            barrier.wait(timeout=10)
            return (
                ConversationService(session)
                .send(_context(session, state), conversation, "hello", "same-request")
                .id
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: send(), range(2)))
    assert first == second
    with state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1
        assert session.scalar(select(func.count()).select_from(ConversationEvent)) == 1
        before = session.scalar(select(func.count()).select_from(TemporalOutboxEntry))
        ConversationAdvanceService(session).advance(state.request.workspace_id, conversation)
        session.rollback()
    with state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 0
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == before
    barrier = threading.Barrier(2)

    def advance():
        with state.sessions.begin() as session:
            barrier.wait(timeout=10)
            ConversationAdvanceService(session).advance(state.request.workspace_id, conversation)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: advance(), range(2)))
    with state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 1
        assert session.get(ConversationTurn, first).status == "running"
        assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) > before


def test_committed_chat_recovers_temporal_unavailability_with_parallel_publishers(
    native_execution, monkeypatch
):
    state = native_execution
    conversation = _conversation(state)
    with state.sessions() as session:
        turn_id = (
            ConversationService(session)
            .send(_context(session, state), conversation, "hello", "recovery")
            .id
        )

    async def scenario():
        async with native_agent_worker(state, OfflineModelProvider(lambda _: None)) as host:
            client = TemporalExecutionClient(host.environment.client, task_queue=host.task_queue)
            publisher = TemporalOutboxPublisher(host.database, client)

            async def unavailable(*args, **kwargs):
                raise ConnectionError("Temporal temporarily unavailable")

            with monkeypatch.context() as patch:
                patch.setattr(client, "dispatch", unavailable)
                assert await publisher.publish_pending() == 0
            with state.sessions.begin() as session:
                rows = session.scalars(select(TemporalOutboxEntry)).all()
                assert rows and all(row.published_at is None for row in rows)
                assert all(row.last_error_code == "ConnectionError" for row in rows)
                for row in rows:
                    row.available_at = datetime.now(UTC)
                requests = [DispatchRequest.model_validate(row.payload) for row in rows]
            await client.dispatch(requests[0])
            publishers = [TemporalOutboxPublisher(host.database, client) for _ in range(2)]
            delivered = await asyncio.gather(*(item.publish_pending() for item in publishers))
            assert sum(delivered) == len(requests)
            for request in requests:
                description = await host.environment.client.get_workflow_handle(
                    request.workflow_id
                ).describe()
                assert description.id == request.workflow_id
            with state.sessions() as session:
                assert session.get(ConversationTurn, turn_id).status == "queued"
                assert all(
                    row.published_at is not None
                    for row in session.scalars(select(TemporalOutboxEntry))
                )

    asyncio.run(scenario())


def test_chat_turns_advance_in_order_through_committed_events_without_maintenance(native_execution):
    state = native_execution
    conversation = _conversation(state)
    with state.sessions() as session:
        service, context = ConversationService(session), _context(session, state)
        first = service.send(context, conversation, "first-question", "first").id
        second = service.send(context, conversation, "second-question", "second").id

    async def scenario():
        seen = []

        class ChatModel(Model):
            def value(self, values):
                assert len(seen) < 2
                value = "first-answer" if not seen else "second-answer"
                if seen:
                    serialized = TypeAdapter(list[TResponseInputItem]).dump_python(
                        values, mode="json"
                    )
                    assert "first-answer" in str(serialized)
                seen.append(value)
                return value

            async def get_response(self, **kwargs):
                return answer(self.value(kwargs["input"]))

            async def stream_response(self, **kwargs):
                value = self.value(kwargs["input"])
                yield stream_delta(value)
                yield stream_item(value)
                yield stream_completed(value)

        async with (
            native_agent_worker(
                state, OfflineModelProvider(lambda _: ChatModel()), stream=True
            ) as host,
            host.worker(),
        ):
            publisher = host.publisher()
            async with asyncio.timeout(60):
                while True:
                    await publisher.publish_pending()
                    with state.sessions() as session:
                        current = session.get(ConversationTurn, second)
                        assert current.status != "failed", current.error_code
                        if current.status == "completed":
                            break
                    await asyncio.sleep(0.05)
            with state.sessions() as session:
                assert session.get(ConversationTurn, first).reply == "first-answer"
                assert session.get(ConversationTurn, second).reply == "second-answer"
                assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 2
            assert seen == ["first-answer", "second-answer"]

    asyncio.run(scenario())


def test_failed_conversation_control_restarts_from_committed_turns(native_execution):
    state = native_execution
    conversation = _conversation(state)
    with state.sessions() as session:
        first = (
            ConversationService(session)
            .send(
                _context(session, state), conversation, "first-question", "before-control-failure"
            )
            .id
        )

    @activity.defn(name="opsmesh.conversation.advance")
    def unavailable(request: ExecutionRequest) -> None:
        raise ApplicationError("Control unavailable", non_retryable=True)

    async def scenario():
        provider = TestModelProvider(
            TestModel.returning_responses([answer("first-answer"), answer("second-answer")])
        )
        async with native_agent_worker(state, provider) as host:
            with state.sessions() as session:
                dispatch = session.scalar(select(TemporalOutboxEntry))
                workflow_id = dispatch.workflow_id
            async with Worker(
                host.environment.client,
                task_queue=host.task_queue,
                workflows=[ConversationWorkflow],
                activities=[unavailable],
                activity_executor=host.executor,
            ):
                await host.publisher().publish_pending()
                failed = host.environment.client.get_workflow_handle(workflow_id)
                with pytest.raises(WorkflowFailureError):
                    await failed.result()
                previous = await failed.describe()
                assert previous.status == WorkflowExecutionStatus.FAILED
            with state.sessions() as session:
                assert session.get(ConversationTurn, first).status == "queued"
                assert session.scalar(select(func.count()).select_from(ConversationExecution)) == 0
                second = (
                    ConversationService(session)
                    .send(
                        _context(session, state),
                        conversation,
                        "second-question",
                        "after-control-failure",
                    )
                    .id
                )
            async with host.worker():
                async with asyncio.timeout(60):
                    while True:
                        await host.publisher().publish_pending()
                        with state.sessions() as session:
                            latest = session.get(ConversationTurn, second)
                            assert latest.status != "failed", latest.error_code
                            if latest.status == "completed":
                                break
                        await asyncio.sleep(0.05)
                recovered = await failed.describe()
                assert recovered.status == WorkflowExecutionStatus.RUNNING
                assert recovered.run_id != previous.run_id
                with state.sessions() as session:
                    assert session.get(ConversationTurn, first).reply == "first-answer"
                    assert session.get(ConversationTurn, second).reply == "second-answer"
                    assert (
                        session.scalar(select(func.count()).select_from(ConversationExecution)) == 2
                    )

    asyncio.run(scenario())


def test_conversation_control_retries_database_failure_beyond_three_attempts(
    native_execution, monkeypatch
):
    state = native_execution
    conversation = _conversation(state)
    with state.sessions() as session:
        turn_id = (
            ConversationService(session)
            .send(_context(session, state), conversation, "recover this turn", "transient-control")
            .id
        )
    advance = ConversationAdvanceService.advance
    attempts = []
    calls = []

    def transient(service, workspace_id, conversation_id):
        attempts.append(conversation_id)
        if len(attempts) <= 4:
            raise OperationalError("Control transaction unavailable", None, ConnectionError())
        return advance(service, workspace_id, conversation_id)

    monkeypatch.setattr(ConversationAdvanceService, "advance", transient)

    class RecoveringModel(Model):
        async def get_response(self, **kwargs):
            calls.append("recovered")
            return answer("recovered")

        async def stream_response(self, **kwargs):
            raise AssertionError("This scenario uses the native non-streaming Model Activity")
            yield

    async def scenario():
        async with (
            native_agent_worker(state, OfflineModelProvider(lambda _: RecoveringModel())) as host,
            host.worker(),
        ):
            async with asyncio.timeout(60):
                while True:
                    await host.publisher().publish_pending()
                    with state.sessions() as session:
                        turn = session.get(ConversationTurn, turn_id)
                        assert turn.status != "failed", turn.error_code
                        if turn.status == "completed":
                            assert turn.reply == "recovered"
                            assert (
                                session.scalar(
                                    select(func.count()).select_from(ConversationExecution)
                                )
                                == 1
                            )
                            break
                    await asyncio.sleep(0.05)
            assert len(attempts) >= 5
            assert calls == ["recovered"]

    asyncio.run(scenario())
