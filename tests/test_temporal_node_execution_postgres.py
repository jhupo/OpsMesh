"""Authored nodes execute through real Temporal and PostgreSQL without a model."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime

import pytest
from agents.models.openai_provider import OpenAIProvider
from sqlalchemy import func, select
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.openai_agents import OpenAIAgentsPlugin
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.capabilities.mcp.models import MCPServer, MCPToolAllowlist, MCPToolCallLog
from opsmesh.capabilities.mcp.temporal_servers import build_temporal_mcp_servers
from opsmesh.governance.costs.models import ModelUsageRecord
from opsmesh.governance.reviews.tool_execution import ToolExecutionReview
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.definitions.activities import OrchestrationNodeActivities
from opsmesh.orchestration.definitions.models import OrchestrationDefinition, OrchestrationRevision
from opsmesh.orchestration.definitions.workflow import OrchestrationNodeWorkflow
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.authorization.snapshot import RunAuthorizationSnapshotService
from opsmesh.orchestration.runs.control import RunControlService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.subworkflow_models import SubworkflowInvocation
from opsmesh.orchestration.runs.temporal import run_execution_reference
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import DispatchRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from tests.fixtures.native_sandbox import native_boundary
from tests.test_native_mcp_protocol import native_mcp_url  # noqa: F401

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


def _node(state, definition):
    with state.sessions() as session:
        task = session.get(Task, state.task_id)
        profile = session.get(AgentProfile, state.profile_id)
        step = TaskStep(
            workspace_id=task.workspace_id,
            task_id=task.id,
            title="Authored node",
            work_package_id="authored",
            assigned_agent_profile_id=profile.id,
            dependencies=definition,
        )
        session.add(step)
        session.flush()
        run = session.get(AgentRun, state.request.resource_id)
        run.task_step_id = step.id
        snapshot = RunAuthorizationSnapshotService(
            session, RunRequestBuilder(session, Settings(environment="test"))
        ).build_authorization_snapshot(task, step, profile)
        run.input = {**run.input, "authorization_snapshot": snapshot}
        session.commit()
        return run_execution_reference(session, run, state.user_id)


def _review(monkeypatch, required=False):
    monkeypatch.setattr(
        "opsmesh.governance.reviews.tool_execution.ToolExecutionReviewService.review_product_tool_call",
        lambda *args, **kwargs: ToolExecutionReview(
            required=required,
            blocked=False,
            risk_level="medium" if required else "low",
            reasons=["offline policy"],
            signals={},
        ),
    )


def _activities(state):
    from opsmesh.orchestration.runs.activities import RunExecutionActivities

    nodes = OrchestrationNodeActivities(
        ActivityDependencies(state.sessions, state.activities._settings)
    )
    return nodes, [
        RunExecutionActivities(state.sessions).release,
        nodes.prepare,
        nodes.complete,
        nodes.cancel,
        nodes.fail,
        state.activities.authorize,
        state.activities.execute_tool,
        state.activities.review_tool,
    ]


async def _wait_approval(state, run_id=None):
    run_id = state.request.resource_id if run_id is None else run_id
    for _ in range(300):
        with state.sessions() as session:
            approval = session.scalar(
                select(Approval).where(
                    Approval.agent_run_id == run_id, Approval.status == "pending"
                )
            )
            if approval:
                assert session.get(AgentRun, run_id).status == "waiting_approval"
                return approval.id
        await asyncio.sleep(0.05)
    raise AssertionError("Node did not reach durable approval wait")


@pytest.mark.parametrize("requires_approval", [False, True])
def test_tool_node_persists_artifact_and_schema_without_model(
    native_execution, monkeypatch, requires_approval
):
    state = native_execution
    _review(monkeypatch, requires_approval)
    request = _node(
        state,
        {
            "node_type": "tool",
            "tool_name": "write_artifact",
            "arguments": {"filename": "node.txt", "content": "Node result"},
            "output_schema": {"type": "object", "required": ["id"]},
        },
    )
    _, activities = _activities(state)

    async def scenario():
        env = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        )
        try:
            async with Worker(
                env.client,
                task_queue="authored-node",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
            ):
                handle = await env.client.start_workflow(
                    OrchestrationNodeWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="authored-node",
                )
                if requires_approval:
                    approval_id = await _wait_approval(state)
                    description = await handle.describe()
                    assert not description.raw_description.pending_activities
                    with state.sessions() as session:
                        ApprovalDecisionService(session).approve(
                            session.get(Approval, approval_id), state.user_id
                        )
                        dispatch = session.scalar(
                            select(TemporalOutboxEntry).where(
                                TemporalOutboxEntry.workflow_id == request.workflow_id,
                                TemporalOutboxEntry.operation == "signal",
                            )
                        )
                        await TemporalExecutionClient(
                            env.client, task_queue="authored-node"
                        ).dispatch(DispatchRequest.model_validate(dispatch.payload))
                await asyncio.wait_for(handle.result(), 30)
        finally:
            await env.shutdown()

    asyncio.run(scenario())
    with state.sessions() as session:
        run = session.get(AgentRun, request.resource_id)
        assert run.status == "completed", run.error
        assert run.output["structured_output"]["value"]["id"]
        assert session.scalar(select(func.count()).select_from(Artifact)) == 1
        assert session.scalar(select(func.count()).select_from(ModelUsageRecord)) == 0


@pytest.mark.parametrize("decision", ["approved", "rejected", "cancelled"])
def test_approval_node_wait_survives_worker_restart_and_closes(native_execution, decision):
    state = native_execution
    request = _node(state, {"node_type": "approval"})
    _, activities = _activities(state)

    async def scenario():
        env = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        )
        try:
            async with Worker(
                env.client,
                task_queue="approval-node",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
            ):
                handle = await env.client.start_workflow(
                    OrchestrationNodeWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="approval-node",
                )
                approval_id = await _wait_approval(state)
                for _ in range(300):
                    if not (await handle.describe()).raw_description.pending_activities:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Approval wait still occupies an Activity slot")
            with state.sessions() as session:
                if decision == "cancelled":
                    await handle.cancel()
                else:
                    service = ApprovalDecisionService(session)
                    approval = session.get(Approval, approval_id)
                    (service.approve if decision == "approved" else service.reject)(
                        approval, state.user_id
                    )
                    dispatch = session.scalar(
                        select(TemporalOutboxEntry).where(
                            TemporalOutboxEntry.workflow_id == request.workflow_id,
                            TemporalOutboxEntry.operation == "signal",
                        )
                    )
                    assert dispatch is not None, "Both approval decisions must wake the Workflow"
                    await TemporalExecutionClient(env.client, task_queue="approval-node").dispatch(
                        DispatchRequest.model_validate(dispatch.payload)
                    )
            async with Worker(
                env.client,
                task_queue="approval-node",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
            ):
                if decision == "cancelled":
                    with pytest.raises(WorkflowFailureError):
                        await asyncio.wait_for(handle.result(), 30)
                else:
                    await asyncio.wait_for(handle.result(), 30)
        finally:
            await env.shutdown()

    asyncio.run(scenario())
    with state.sessions() as session:
        assert (
            session.get(AgentRun, request.resource_id).status
            == {"approved": "completed", "rejected": "failed", "cancelled": "cancelled"}[decision]
        )


def test_node_rejects_changed_frozen_definition_without_executing(native_execution, monkeypatch):
    state = native_execution
    _review(monkeypatch)
    request = _node(
        state,
        {
            "node_type": "tool",
            "tool_name": "write_artifact",
            "arguments": {"filename": "node.txt", "content": "Authorized"},
        },
    )
    with state.sessions() as session:
        run = session.get(AgentRun, request.resource_id)
        step = session.get(TaskStep, run.task_step_id)
        step.dependencies = {
            **step.dependencies,
            "arguments": {"filename": "changed.txt", "content": "Changed"},
        }
        session.commit()
    _, activities = _activities(state)

    async def scenario():
        env = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        )
        try:
            async with Worker(
                env.client,
                task_queue="changed-node",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
            ):
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(
                        env.client.execute_workflow(
                            OrchestrationNodeWorkflow.run,
                            request,
                            id=request.workflow_id,
                            task_queue="changed-node",
                        ),
                        30,
                    )
        finally:
            await env.shutdown()

    asyncio.run(scenario())
    with state.sessions() as session:
        assert session.get(AgentRun, request.resource_id).status == "failed"
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0


@pytest.mark.parametrize("source", ["parent_api", "parent_temporal", "child_api"])
def test_subworkflow_cancellation_closes_entire_execution_tree(native_execution, source):
    state = native_execution
    leaf = _node(state, {"node_type": "approval"})
    with state.sessions() as session:
        child_task = session.get(Task, state.task_id)
        definition = OrchestrationDefinition(
            workspace_id=leaf.workspace_id, key="cancel-tree", name="Cancel tree"
        )
        session.add(definition)
        session.flush()
        parent_requests = []
        for index in range(2):
            parent_task = Task(
                workspace_id=leaf.workspace_id,
                created_by_user_id=state.user_id,
                owner_agent_profile_id=state.profile_id,
                title=f"Parent {index}",
                status="running",
                execution_identity=child_task.execution_identity,
            )
            session.add(parent_task)
            session.flush()
            parent_step = TaskStep(
                workspace_id=leaf.workspace_id,
                task_id=parent_task.id,
                title="Child",
                work_package_id="child",
                status="running",
                assigned_agent_profile_id=state.profile_id,
                dependencies={
                    "node_type": "subworkflow",
                    "subworkflow_definition_id": str(definition.id),
                },
            )
            session.add(parent_step)
            session.flush()
            profile = session.get(AgentProfile, state.profile_id)
            snapshot = RunAuthorizationSnapshotService(
                session, RunRequestBuilder(session, Settings(environment="test"))
            ).build_authorization_snapshot(parent_task, parent_step, profile)
            parent_run = AgentRun(
                workspace_id=leaf.workspace_id,
                task_id=parent_task.id,
                task_step_id=parent_step.id,
                agent_profile_id=state.profile_id,
                model="test",
                status="waiting_subworkflow",
                input={"authorization_snapshot": snapshot},
                started_at=datetime.now(UTC),
            )
            session.add(parent_run)
            session.flush()
            session.add(
                SubworkflowInvocation(
                    workspace_id=leaf.workspace_id,
                    parent_task_id=parent_task.id,
                    parent_task_step_id=parent_step.id,
                    parent_run_id=parent_run.id,
                    child_task_id=child_task.id,
                    definition_id=definition.id,
                    definition_version=1,
                    status="running",
                )
            )
            session.flush()
            parent_requests.append(run_execution_reference(session, parent_run, state.user_id))
            child_task = parent_task
        session.commit()
    _, activities = _activities(state)

    async def scenario():
        env = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        )
        try:
            async with Worker(
                env.client,
                task_queue="cancel-tree",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
            ):
                requests = [leaf, *parent_requests]
                handles = [
                    await env.client.start_workflow(
                        OrchestrationNodeWorkflow.run,
                        request,
                        id=request.workflow_id,
                        task_queue="cancel-tree",
                    )
                    for request in requests
                ]
                await _wait_approval(state)
                if source == "parent_temporal":
                    await handles[-1].cancel()
                    with pytest.raises(WorkflowFailureError):
                        await asyncio.wait_for(handles[-1].result(), 30)
                else:
                    target = requests[0 if source == "child_api" else -1]
                    with state.sessions() as session:
                        RunControlService(session).cancel_run(
                            workspace_id=leaf.workspace_id,
                            run_id=target.resource_id,
                            actor_user_id=state.user_id,
                        )
                with state.sessions() as session:
                    assert set(session.scalars(select(AgentRun.status))) == {"cancelled"}
                    assert set(session.scalars(select(Task.status))) == {"cancelled"}
                    assert set(session.scalars(select(SubworkflowInvocation.status))) == {
                        "cancelled"
                    }
                    commands = session.scalars(
                        select(TemporalOutboxEntry).where(
                            TemporalOutboxEntry.operation.in_(("cancel", "signal"))
                        )
                    ).all()
                    dispatcher = TemporalExecutionClient(env.client, task_queue="cancel-tree")
                    for command in commands:
                        await dispatcher.dispatch(DispatchRequest.model_validate(command.payload))
                for handle in handles:
                    with suppress(WorkflowFailureError):
                        await asyncio.wait_for(handle.result(), 30)
        finally:
            await env.shutdown()

    asyncio.run(scenario())


def test_authored_mcp_node_uses_native_protocol_and_frozen_scoped_factory(
    native_execution,
    native_mcp_url,  # noqa: F811
    monkeypatch,
):
    state = native_execution
    with state.sessions() as session:
        server = MCPServer(
            workspace_id=state.request.workspace_id,
            name="Echo",
            server_type="streamable_http",
            connection={"url": native_mcp_url},
            health_status="healthy",
            last_health_check_at=datetime.now(UTC),
        )
        session.add(server)
        session.flush()
        session.add(
            MCPToolAllowlist(
                workspace_id=state.request.workspace_id,
                mcp_server_id=server.id,
                tool_name="echo",
                input_schema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            )
        )
        profile = session.get(AgentProfile, state.profile_id)
        profile.tool_policy = {"allowed_tools": ["echo"]}
        session.commit()
    # The offline fixture is a trusted loopback server; authorization, native protocol,
    # factory references, Activity boundaries and durable evidence remain production code.
    monkeypatch.setattr(
        "opsmesh.capabilities.mcp.temporal_servers.validate_mcp_url", lambda *a, **k: None
    )
    request = _node(
        state, {"node_type": "mcp", "tool_name": "echo", "arguments": {"message": "Node protocol"}}
    )
    _, activities = _activities(state)
    _, _, backends = native_boundary()
    factories = build_temporal_mcp_servers(state.sessions, state.activities._settings, backends)

    async def scenario():
        env = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        )
        try:
            async with Worker(
                env.client,
                task_queue="mcp-node",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
                plugins=[
                    OpenAIAgentsPlugin(
                        model_provider=OpenAIProvider(api_key="offline-unused"),
                        mcp_servers=factories,
                    )
                ],
            ):
                await asyncio.wait_for(
                    env.client.execute_workflow(
                        OrchestrationNodeWorkflow.run,
                        request,
                        id=request.workflow_id,
                        task_queue="mcp-node",
                    ),
                    30,
                )
        finally:
            await env.shutdown()

    asyncio.run(scenario())
    with state.sessions() as session:
        assert session.get(AgentRun, request.resource_id).status == "completed"
        log = session.scalars(select(MCPToolCallLog)).one()
        assert log.status == "completed"
        assert log.tool_call_id.startswith(f"node:{request.resource_id}:")


@pytest.mark.parametrize("source", ["run", "task"])
def test_cancelling_task_run_stops_siblings_once_and_preserves_other_tasks(
    native_execution, source
):
    state = native_execution
    request = state.request
    with state.sessions() as session:
        unrelated_task = Task(
            workspace_id=request.workspace_id,
            created_by_user_id=state.user_id,
            title="Unrelated execution",
            status="running",
        )
        session.add(unrelated_task)
        session.flush()
        sibling = AgentRun(
            workspace_id=request.workspace_id,
            agent_profile_id=state.profile_id,
            task_id=state.task_id,
            status="running",
        )
        unrelated = AgentRun(
            workspace_id=request.workspace_id,
            agent_profile_id=state.profile_id,
            task_id=unrelated_task.id,
            status="running",
        )
        historical = AgentRun(
            workspace_id=request.workspace_id,
            agent_profile_id=state.profile_id,
            task_id=unrelated_task.id,
            status="completed",
        )
        queued_step = TaskStep(
            workspace_id=request.workspace_id,
            task_id=state.task_id,
            title="Not started",
            status="queued",
        )
        session.add_all([sibling, unrelated, historical, queued_step])
        session.commit()
        control = RunControlService(session)
        if source == "task":
            control.cancel_task(
                workspace_id=request.workspace_id,
                task_id=state.task_id,
                actor_user_id=state.user_id,
            )
        else:
            for _ in range(2):
                control.cancel_run(
                    workspace_id=request.workspace_id,
                    run_id=request.resource_id,
                    actor_user_id=state.user_id,
                )
        control.cancel_run(
            workspace_id=request.workspace_id,
            run_id=historical.id,
            actor_user_id=state.user_id,
        )
        session.refresh(sibling)
        session.refresh(unrelated)
        session.refresh(unrelated_task)
        session.refresh(queued_step)
        assert sibling.status == "cancelled"
        assert unrelated.status == "running"
        assert unrelated_task.status == "running"
        assert queued_step.status == "cancelled"
        commands = session.scalars(
            select(TemporalOutboxEntry).where(TemporalOutboxEntry.operation == "cancel")
        ).all()
        assert len(commands) == 2
        assert len({command.workflow_id for command in commands}) == 2


def test_subworkflow_native_launch_child_approval_and_parent_completion(native_execution):
    state = native_execution
    with state.sessions() as session:
        team = AgentTeam(
            workspace_id=state.request.workspace_id,
            name="Subworkflow team",
            manager_agent_profile_id=state.profile_id,
        )
        definition = OrchestrationDefinition(
            workspace_id=state.request.workspace_id,
            key="published-child",
            name="Published child",
            status="published",
        )
        session.add_all([team, definition])
        session.flush()
        session.add_all(
            [
                AgentTeamMember(
                    workspace_id=team.workspace_id,
                    agent_team_id=team.id,
                    agent_profile_id=state.profile_id,
                    team_role="expert",
                    max_concurrent_tasks=3,
                ),
                OrchestrationRevision(
                    workspace_id=team.workspace_id,
                    definition_id=definition.id,
                    version=1,
                    name=definition.name,
                    definition={
                        "nodes": [
                            {
                                "package_id": "child-approval",
                                "node_type": "approval",
                                "title": "Child approval",
                                "required_role": "expert",
                            }
                        ]
                    },
                ),
            ]
        )
        task = session.get(Task, state.task_id)
        task.agent_team_id = team.id
        session.commit()
    parent = _node(
        state,
        {
            "node_type": "subworkflow",
            "subworkflow_definition_id": str(definition.id),
            "subworkflow_version": 1,
        },
    )
    _, activities = _activities(state)

    async def scenario():
        env = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter
        )
        try:
            async with Worker(
                env.client,
                task_queue="native-subworkflow",
                workflows=[OrchestrationNodeWorkflow],
                activities=activities,
                activity_executor=ThreadPoolExecutor(4),
            ):
                handle = await env.client.start_workflow(
                    OrchestrationNodeWorkflow.run,
                    parent,
                    id=parent.workflow_id,
                    task_queue="native-subworkflow",
                )
                for _ in range(300):
                    with state.sessions() as session:
                        invocation = session.scalar(select(SubworkflowInvocation))
                        if invocation is not None:
                            child_run = session.scalar(
                                select(AgentRun).where(AgentRun.task_id == invocation.child_task_id)
                            )
                            child = run_execution_reference(session, child_run, state.user_id)
                            command = session.scalar(
                                select(TemporalOutboxEntry).where(
                                    TemporalOutboxEntry.workflow_id == child.workflow_id,
                                    TemporalOutboxEntry.operation == "start",
                                )
                            )
                            assert command is not None
                            await TemporalExecutionClient(
                                env.client, task_queue="native-subworkflow"
                            ).dispatch(DispatchRequest.model_validate(command.payload))
                            break
                    if (await handle.describe()).status != WorkflowExecutionStatus.RUNNING:
                        await handle.result()
                        raise AssertionError("Parent ended before creating child execution")
                    await asyncio.sleep(0.05)
                else:
                    raise AssertionError("Parent did not publish child execution")
                approval_id = await _wait_approval(state, child.resource_id)
                with state.sessions() as session:
                    ApprovalDecisionService(session).approve(
                        session.get(Approval, approval_id), state.user_id, reason="Approve child"
                    )
                    session.commit()
                    commands = session.scalars(
                        select(TemporalOutboxEntry).where(
                            TemporalOutboxEntry.workflow_id == child.workflow_id,
                            TemporalOutboxEntry.operation == "signal",
                        )
                    ).all()
                    for command in commands:
                        await TemporalExecutionClient(
                            env.client, task_queue="native-subworkflow"
                        ).dispatch(DispatchRequest.model_validate(command.payload))
                await asyncio.wait_for(
                    env.client.get_workflow_handle(child.workflow_id).result(), 30
                )
                with state.sessions() as session:
                    commands = session.scalars(
                        select(TemporalOutboxEntry).where(
                            TemporalOutboxEntry.workflow_id == parent.workflow_id,
                            TemporalOutboxEntry.operation == "signal",
                        )
                    ).all()
                    assert commands
                    for command in commands:
                        await TemporalExecutionClient(
                            env.client, task_queue="native-subworkflow"
                        ).dispatch(DispatchRequest.model_validate(command.payload))
                await asyncio.wait_for(handle.result(), 30)
        finally:
            await env.shutdown()

    asyncio.run(scenario())
    with state.sessions() as session:
        assert set(session.scalars(select(AgentRun.status))) == {"completed"}
        assert set(session.scalars(select(Task.status))) == {"completed"}
        assert session.scalar(select(SubworkflowInvocation)).status == "completed"
