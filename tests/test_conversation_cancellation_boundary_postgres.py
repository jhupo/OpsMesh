"""A cancelled Turn releases its live Activity before the next Turn can start."""

import asyncio
import os
import selectors
import sys

import pytest
from agents import Model
from sqlalchemy import select
from temporalio.client import WorkflowFailureError

from opsmesh.agents.execution.providers.openai.workflow import AgentWorkflow
from opsmesh.identity.authorization.context import AuthenticatedUser, WorkspaceContext
from opsmesh.identity.users.models import User
from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.orchestration.conversations.models import ConversationExecution, ConversationTurn
from opsmesh.orchestration.conversations.schemas import ConversationCreateRequest
from opsmesh.orchestration.conversations.service import ConversationService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.native_agent_worker import OfflineModelProvider, answer, native_agent_worker

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


def test_cancelled_turn_blocks_next_turn_until_native_activity_cleanup_finishes(native_execution):
    state = native_execution

    def context(session):
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

    with state.sessions() as session:
        service = ConversationService(session)
        conversation = service.create(
            context(session),
            ConversationCreateRequest(
                mode="agent",
                agent_profile_id=state.profile_id,
            ),
        )
        first = service.send(context(session), conversation.id, "First", "first")
        second = service.send(context(session), conversation.id, "Second", "second")

    def advance():
        with state.sessions.begin() as session:
            ConversationAdvanceService(session).advance(state.request.workspace_id, conversation.id)
            return session.scalar(
                select(ConversationExecution.task_id).where(
                    ConversationExecution.workspace_id == state.request.workspace_id,
                    ConversationExecution.turn_id == second.id,
                )
            )

    async def scenario():
        started, cancelling, allow_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        class BlockingModel(Model):
            async def get_response(self, **kwargs):
                calls.append("model")
                if len(calls) == 1:
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelling.set()
                        await allow_cleanup.wait()
                return answer("second finished")

            async def stream_response(self, **kwargs):
                raise AssertionError("Non-streaming product validation")
                yield

        async with (
            native_agent_worker(state, OfflineModelProvider(lambda _: BlockingModel())) as host,
            host.worker(),
        ):
            advance()
            with state.sessions() as session:
                task_id = session.scalar(
                    select(ConversationExecution.task_id).where(
                        ConversationExecution.turn_id == first.id,
                        ConversationExecution.workspace_id == state.request.workspace_id,
                    )
                )
                run_id = session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.task_id == task_id,
                        AgentRun.workspace_id == state.request.workspace_id,
                    )
                )
            request = ExecutionRequest(
                execution_id=run_id,
                resource_id=run_id,
                workspace_id=state.request.workspace_id,
                kind=ExecutionKind.AGENT,
                requested_by_user_id=state.user_id,
            )
            handle = await host.environment.client.start_workflow(
                AgentWorkflow.run,
                request,
                id=request.workflow_id,
                task_queue=host.task_queue,
            )
            try:
                await asyncio.wait_for(started.wait(), 30)
                with state.sessions() as session:
                    ConversationService(session).cancel(context(session), conversation.id, first.id)
                assert advance() is None
                with state.sessions() as session:
                    assert session.get(ConversationTurn, second.id).status == "queued"
                    assert session.get(AgentRun, run_id).execution_released_at is None
                await handle.cancel()
                await asyncio.wait_for(cancelling.wait(), 20)
                assert advance() is None
                assert calls == ["model"]
                allow_cleanup.set()
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), 30)
                with state.sessions() as session:
                    assert session.get(AgentRun, run_id).execution_released_at is not None
                assert advance() is not None
            finally:
                allow_cleanup.set()

    with asyncio.Runner(
        loop_factory=(lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        if sys.platform == "win32"
        else None
    ) as runner:
        runner.run(scenario())
