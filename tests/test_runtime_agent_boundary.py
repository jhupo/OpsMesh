"""Native Activity authorization and frozen references, without the retired RPC host."""

import asyncio
import os
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import func, select
    from temporalio.openai_agents.testing import TestModel, TestModelProvider

    from opsmesh.agents.execution.contracts import ToolExecutionResult
    from opsmesh.agents.execution.providers.openai.tools_temporal import AgentToolOperation
    from opsmesh.orchestration.runs.control import RunControlService
    from opsmesh.orchestration.runs.models import AgentRun
    from opsmesh.resources.artifacts.models import Artifact
    from tests.fixtures.native_agent_worker import native_agent_worker

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


@workflow.defn
class ToolBoundaryWorkflow:
    @workflow.run
    async def run(self, operation: AgentToolOperation) -> ToolExecutionResult:
        return await workflow.execute_activity(
            "opsmesh.agent.execute_tool",
            operation,
            result_type=ToolExecutionResult,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@pytest.mark.parametrize("mutation", ["workspace", "run", "scope", "tool"])
def test_activity_rejects_foreign_identity_or_tool_scope_before_effects(native_execution, mutation):
    state = native_execution

    async def scenario():
        definition = await state.activities.prepare(state.request)
        context = definition.context
        tool_name = "write_artifact"
        if mutation == "workspace":
            context = replace(context, workspace_id=uuid4())
        elif mutation == "run":
            context = replace(context, run_id=uuid4())
        elif mutation == "scope":
            context = replace(
                context, metadata={**context.metadata, "capability_catalog_fingerprint": "altered"}
            )
        else:
            tool_name = "unauthorized"
            context = replace(context, allowed_tools=(*context.allowed_tools, tool_name))
        operation = AgentToolOperation(
            context=context,
            tool_name=tool_name,
            tool_call_id="foreign-effect",
            arguments={"filename": "unapproved.txt", "content": "must not exist"},
        )
        async with (
            native_agent_worker(
                state, TestModelProvider(TestModel.returning_responses([]))
            ) as host,
            host.worker(additional_workflows=[ToolBoundaryWorkflow]),
        ):
            if mutation == "tool":
                result = await host.environment.client.execute_workflow(
                    ToolBoundaryWorkflow.run,
                    operation,
                    id=f"boundary-{uuid4()}",
                    task_queue=host.task_queue,
                )
                assert result.status == "failed"
                assert result.error["code"] == "tool_not_in_run_manifest"
            else:
                with pytest.raises(WorkflowFailureError):
                    await host.environment.client.execute_workflow(
                        ToolBoundaryWorkflow.run,
                        operation,
                        id=f"boundary-{uuid4()}",
                        task_queue=host.task_queue,
                    )

    asyncio.run(scenario())
    with state.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0


def test_workflow_definition_contains_references_without_credentials_or_live_connections(
    native_execution,
):
    state = native_execution
    definition = asyncio.run(state.activities.prepare(state.request))
    payload = definition.model_dump_json()
    assert definition.model_reference.workspace_id == state.request.workspace_id
    assert definition.model_reference.run_id == state.request.resource_id
    assert definition.session.workspace_id == state.request.workspace_id
    assert "sk-unit-test-provider" not in payload
    assert "database_url" not in payload and "container_id" not in payload


def test_tool_rechecks_committed_cancellation_before_writing(native_execution, monkeypatch):
    state = native_execution

    async def scenario():
        definition = await state.activities.prepare(state.request)
        authorized, release = asyncio.Event(), asyncio.Event()
        authorize = state.activities.authorize

        @activity.defn(name="opsmesh.agent.authorize")
        async def wait_after_authorization(operation: AgentToolOperation) -> None:
            await authorize(operation)
            authorized.set()
            await release.wait()

        monkeypatch.setattr(state.activities, "authorize", wait_after_authorization)
        async with (
            native_agent_worker(
                state, TestModelProvider(TestModel.returning_responses([]))
            ) as host,
            host.worker(additional_workflows=[ToolBoundaryWorkflow]),
        ):
            handle = await host.environment.client.start_workflow(
                ToolBoundaryWorkflow.run,
                AgentToolOperation(
                    context=definition.context,
                    tool_name="write_artifact",
                    tool_call_id="cancelled-before-write",
                    arguments={"filename": "cancelled.txt", "content": "must not exist"},
                ),
                id=f"tool-cancellation-{uuid4()}",
                task_queue=host.task_queue,
            )
            await asyncio.wait_for(authorized.wait(), 30)
            with state.sessions() as session:
                RunControlService(session).cancel_run(
                    workspace_id=state.request.workspace_id,
                    run_id=state.request.resource_id,
                    actor_user_id=state.user_id,
                )
            release.set()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), 30)

    asyncio.run(scenario())
    with state.sessions() as session:
        assert session.get(AgentRun, state.request.resource_id).status == "cancelled"
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
