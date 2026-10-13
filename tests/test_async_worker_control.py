"""One native Worker runs concurrent SDK Tasks and resumes durable approvals."""

import asyncio
import json
import os

import pytest
from agents import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall
from sqlalchemy import func, select
from temporalio.client import WorkflowFailureError
from temporalio.openai_agents.testing import TestModel, TestModelProvider

from opsmesh.agents.execution.providers.openai.workflow import AgentWorkflow
from opsmesh.agents.sessions.models import SDKSessionMessageRecord
from opsmesh.governance.reviews.tool_execution import ToolExecutionReview
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.runs.models import AgentResumeStateSnapshot, AgentRun
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest, WorkflowSignal
from tests.fixtures.native_agent_worker import OfflineModelProvider, answer, native_agent_worker

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


def test_concurrent_run_completes_while_another_waits_and_cancels_independently(native_execution):
    state = native_execution
    with state.sessions.begin() as session:
        task = Task(
            workspace_id=state.request.workspace_id,
            created_by_user_id=state.user_id,
            owner_agent_profile_id=state.profile_id,
            execution_identity=ExecutionIdentityService(session).capture(
                state.request.workspace_id, state.user_id
            ),
            title="Concurrent fast request",
            status="queued",
        )
        session.add(task)
        session.flush()
        second_run = RunOrchestrationService(session).create_queued_run_for_task(task)
        second = ExecutionRequest(
            execution_id=second_run.id,
            workspace_id=state.request.workspace_id,
            resource_id=second_run.id,
            kind=ExecutionKind.AGENT,
            requested_by_user_id=state.user_id,
        )

    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        class SlowModel(Model):
            async def get_response(self, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            async def stream_response(self, **kwargs):
                raise AssertionError("This scenario uses the nonstreaming native model Activity")
                yield

        class FastModel(Model):
            async def get_response(self, **kwargs):
                return answer("concurrent answer")

            async def stream_response(self, **kwargs):
                raise AssertionError("This scenario uses the nonstreaming native model Activity")
                yield

        provider = OfflineModelProvider(
            lambda reference: (
                SlowModel() if reference.run_id == state.request.resource_id else FastModel()
            )
        )
        async with native_agent_worker(state, provider) as host, host.worker():
            slow = await host.environment.client.start_workflow(
                AgentWorkflow.run,
                state.request,
                id=state.request.workflow_id,
                task_queue=host.task_queue,
            )
            await asyncio.wait_for(started.wait(), 30)
            await asyncio.wait_for(
                host.environment.client.execute_workflow(
                    AgentWorkflow.run,
                    second,
                    id=second.workflow_id,
                    task_queue=host.task_queue,
                ),
                30,
            )
            assert not stopped.is_set()
            with state.sessions() as session:
                assert session.get(AgentRun, state.request.resource_id).status == "running"
                run = session.get(AgentRun, second.resource_id)
                assert run.status == "completed"
                messages = session.scalars(
                    select(SDKSessionMessageRecord.message_data).where(
                        SDKSessionMessageRecord.session_id == run.session_key
                    )
                ).all()
                assert "concurrent answer" in str(messages)
            await slow.cancel()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(slow.result(), 15)
            assert stopped.is_set()

    asyncio.run(scenario())
    with state.sessions() as session:
        assert session.get(AgentRun, state.request.resource_id).status == "cancelled"
        assert session.get(AgentRun, second.resource_id).status == "completed"


def test_native_approval_resume_survives_worker_restart_with_encrypted_evidence(
    native_execution, monkeypatch
):
    state = native_execution
    monkeypatch.setattr(
        "opsmesh.governance.reviews.tool_execution.ToolExecutionReviewService.review_product_tool_call",
        lambda self, **kwargs: ToolExecutionReview(
            required=True, blocked=False, risk_level="high", reasons=["manual"], signals={}
        ),
    )
    original_call = "original-write"
    responses = [
        ModelResponse(
            output=[
                ResponseFunctionToolCall(
                    id="write-item",
                    type="function_call",
                    name="write_artifact",
                    call_id=original_call,
                    arguments=json.dumps(
                        {"filename": "approved.txt", "content": "secret-checkpoint-evidence"}
                    ),
                )
            ],
            usage=Usage(),
            response_id="tool-response",
        ),
        answer("resumed answer"),
    ]

    async def scenario():
        async with native_agent_worker(
            state, TestModelProvider(TestModel.returning_responses(responses))
        ) as host:
            async with host.worker():
                handle = await host.environment.client.start_workflow(
                    AgentWorkflow.run,
                    state.request,
                    id=state.request.workflow_id,
                    task_queue=host.task_queue,
                )

                async def paused():
                    while True:
                        with state.sessions() as session:
                            run = session.get(AgentRun, state.request.resource_id)
                            if run.status == "waiting_approval":
                                snapshot = session.scalar(select(AgentResumeStateSnapshot))
                                assert "secret-checkpoint-evidence" not in snapshot.encrypted_state
                                return session.scalar(select(Approval.id))
                            assert run.status not in {"failed", "cancelled", "completed"}
                        await asyncio.sleep(0.05)

                approval_id = await asyncio.wait_for(paused(), 30)
            with state.sessions() as session:
                ApprovalDecisionService(session).approve(
                    session.get(Approval, approval_id), state.user_id, "Inspected"
                )
            await handle.signal("approval", WorkflowSignal(resource_id=approval_id))
            await handle.signal("approval", WorkflowSignal(resource_id=approval_id))
            async with host.worker():
                await asyncio.wait_for(handle.result(), 30)

    asyncio.run(scenario())
    with state.sessions() as session:
        assert session.get(AgentRun, state.request.resource_id).status == "completed"
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert all(
            snapshot.status == "consumed"
            for snapshot in session.scalars(select(AgentResumeStateSnapshot))
        )
