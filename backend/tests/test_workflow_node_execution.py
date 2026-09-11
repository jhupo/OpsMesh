import asyncio
from uuid import uuid4

import pytest

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRuntimeContext,
    AgentRuntimeToolResult,
)
from backend.app.agents.models import AgentProfile
from backend.app.orchestration.models import SubworkflowInvocation
from backend.app.orchestration.run_eligibility import RunEligibilityService
from backend.app.orchestration.run_execution import RunExecutionDependencies, RunExecutionService
from backend.app.orchestration.subworkflows import (
    SubworkflowExecutionError,
    SubworkflowExecutionService,
)
from backend.app.planning.workflow_contracts import WorkflowNode
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus
from backend.app.tasks.models import Task, TaskStep
from backend.app.workers.jobs import JobPayload, JobType
from backend.tests.test_worker_run_execution import _seed_workspace, _session


class _ToolExecutor:
    def review_tool_call(self, **kwargs: object) -> dict[str, object]:
        return {"decision": "allow"}

    async def execute_tool(self, **kwargs: object) -> AgentRuntimeToolResult:
        return AgentRuntimeToolResult(status="completed", output={"ok": True})


def test_typed_workflow_nodes_require_explicit_execution_targets() -> None:
    tool = WorkflowNode(
        package_id="echo",
        title="Echo",
        node_type="tool",
        tool_name="echo",
        required_tools=["echo"],
    )
    assert tool.node_type == "tool"
    with pytest.raises(ValueError, match="tool_name"):
        WorkflowNode(package_id="missing", title="Missing", node_type="tool")
    with pytest.raises(ValueError, match="definition"):
        WorkflowNode(package_id="child", title="Child", node_type="subworkflow")


def test_direct_tool_node_uses_authorized_executor_without_model_execution() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Tool task")
    session.add(task)
    session.flush()
    step = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Call tool",
        work_package_id="call-tool",
        status="running",
        dependencies={"node_type": "tool", "tool_name": "echo", "arguments": {"value": 1}},
    )
    session.add(step)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=step.id,
        status="running",
        input={},
    )
    session.add(run)
    session.flush()
    context = AgentRuntimeContext(workspace_id=workspace.id, task_id=task.id, run_id=run.id)
    request = AgentRunRequest(
        agent_profile=AgentProfile(workspace_id=workspace.id, name="Tool", role="worker"),
        input_text="",
        context=context,
        tool_executor=_ToolExecutor(),
    )
    job = JobPayload(
        workspace_id=workspace.id,
        job_type=JobType.AGENT_RUN,
        resource_id=run.id,
        idempotency_key=str(uuid4()),
    )
    result = asyncio.run(
        RunExecutionService(
            session,
            RunExecutionDependencies(lifecycle=None),  # type: ignore[arg-type]
        )._run_non_agent_node(run, job, request, "tool")
    )
    assert result is not None
    assert result.status == "completed"
    assert result.output == {"ok": True}


def test_control_only_workflow_finishes_after_control_nodes_complete() -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    task = Task(workspace_id=workspace.id, created_by_user_id=user.id, title="Control task")
    session.add(task)
    session.flush()
    session.add_all(
        [
            TaskStep(
                workspace_id=workspace.id,
                task_id=task.id,
                title="Start",
                work_package_id="start",
                dependencies={"node_type": "start", "after_step_ids": []},
            ),
            TaskStep(
                workspace_id=workspace.id,
                task_id=task.id,
                title="End",
                work_package_id="end",
                dependencies={"node_type": "end", "after_step_ids": []},
            ),
        ]
    )
    session.flush()
    assert RunEligibilityService(session).next_eligible_steps(task.id, workspace.id) == []
    assert task.status == "completed"


def test_subworkflow_invocation_is_idempotent_and_workspace_scoped() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, with_default_provider=False)
    parent_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Parent task",
        agent_team_id=None,
    )
    child_task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Child task",
        status="completed",
        final_output={"summary": "child complete"},
    )
    session.add_all([parent_task, child_task])
    session.flush()
    parent_step = TaskStep(
        workspace_id=workspace.id,
        task_id=parent_task.id,
        title="Invoke child",
        work_package_id="invoke-child",
        dependencies={"node_type": "subworkflow"},
    )
    parent_run = AgentRun(
        workspace_id=workspace.id,
        task_id=parent_task.id,
        task_step_id=parent_step.id,
        status=RunStatus.WAITING_SUBWORKFLOW.value,
        input={},
    )
    child_run = AgentRun(
        workspace_id=workspace.id,
        task_id=child_task.id,
        status=RunStatus.COMPLETED.value,
        input={},
    )
    session.add_all([parent_step, parent_run, child_run])
    session.flush()
    invocation = SubworkflowInvocation(
        workspace_id=workspace.id,
        parent_task_id=parent_task.id,
        parent_task_step_id=parent_step.id,
        parent_run_id=parent_run.id,
        child_task_id=child_task.id,
        definition_id=uuid4(),
        definition_version=1,
        status="running",
        input_payload={},
    )
    session.add(invocation)
    session.flush()

    launch = SubworkflowExecutionService(session).launch(
        parent_run=parent_run,
        parent_task=parent_task,
        parent_step=parent_step,
        requested_by_user_id=user.id,
    )
    assert launch.status == "waiting_subworkflow"
    assert launch.invocation_id == invocation.id
    assert launch.child_task_id == child_task.id
    assert launch.child_run_id == child_run.id
    assert session.query(SubworkflowInvocation).count() == 1

    with pytest.raises(SubworkflowExecutionError):
        SubworkflowExecutionService._assert_payload_bound({"value": "x" * (64 * 1024)})
