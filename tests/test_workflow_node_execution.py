import pytest

from opsmesh.agents.execution.contracts import (
    AgentRunResult,
    AgentStructuredOutput,
    ToolExecutionResult,
)
from opsmesh.orchestration.definitions.conditions import evaluate_task_step_condition
from opsmesh.orchestration.definitions.contracts import WorkflowNode
from opsmesh.orchestration.definitions.data import (
    WorkflowDataBindingError,
    resolve_workflow_inputs,
)
from opsmesh.orchestration.definitions.models import OrchestrationDefinition
from opsmesh.orchestration.definitions.subworkflows import (
    SubworkflowExecutionError,
    SubworkflowExecutionService,
)
from opsmesh.orchestration.runs.eligibility import RunEligibilityService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.state import RunStatus
from opsmesh.orchestration.runs.steps.completion import TaskStepCompletionService
from opsmesh.orchestration.runs.subworkflow_models import SubworkflowInvocation
from opsmesh.orchestration.tasks.models import Task, TaskStep
from tests.fixtures.agent_product import _seed_workspace, _session


class _ToolExecutor:
    async def review_tool_call(self, **kwargs: object) -> dict[str, object]:
        return {"decision": "allow"}

    async def execute_tool(self, **kwargs: object) -> ToolExecutionResult:
        return ToolExecutionResult(status="completed", output={"ok": True})


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
    node = WorkflowNode(
        package_id="bound",
        title="Bound",
        input_bindings={"source": {"reference": "task.input.customer"}},
        output_schema={"type": "object", "required": ["ok"]},
    )
    assert node.input_bindings["source"].reference == "task.input.customer"
    optional = WorkflowNode(
        package_id="optional",
        title="Optional",
        input_bindings={"source": {"reference": "task.input.customer", "required": False}},
    )
    assert optional.input_bindings["source"].required is False


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
    session.add(parent_step)
    session.flush()
    parent_run.task_step_id = parent_step.id
    session.add_all([parent_run, child_run])
    session.flush()
    definition = OrchestrationDefinition(workspace_id=workspace.id, key="child", name="Child")
    session.add(definition)
    session.flush()
    invocation = SubworkflowInvocation(
        workspace_id=workspace.id,
        parent_task_id=parent_task.id,
        parent_task_step_id=parent_step.id,
        parent_run_id=parent_run.id,
        child_task_id=child_task.id,
        definition_id=definition.id,
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


def test_workflow_data_bindings_resolve_task_and_completed_step_outputs() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, with_default_provider=False)
    task = Task(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        title="Data task",
        input={"customer": {"id": "c-1"}},
    )
    session.add(task)
    session.flush()
    source = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Source",
        work_package_id="source",
        status="completed",
        result_payload={"structured_output": {"value": {"score": 8}}},
    )
    target = TaskStep(
        workspace_id=workspace.id,
        task_id=task.id,
        title="Target",
        work_package_id="target",
        dependencies={
            "input_bindings": {
                "customer_id": {"reference": "task.input.customer.id"},
                "score": {"reference": "steps.source.output.structured_output.value.score"},
            }
        },
    )
    session.add_all([source, target])
    session.flush()
    assert resolve_workflow_inputs(session, task, target) == {
        "customer_id": "c-1",
        "score": 8,
    }
    target.dependencies = {
        "input_bindings": {"missing": {"reference": "steps.source.output.missing"}}
    }
    with pytest.raises(WorkflowDataBindingError, match="missing"):
        resolve_workflow_inputs(session, task, target)

    target.dependencies = {
        "condition": {
            "path": "steps.source.output.structured_output.value.score",
            "operator": "greater_than",
            "value": 5,
        }
    }
    assert evaluate_task_step_condition(session, task, target).state == "true"

    target.dependencies = {
        "output_schema": {
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
    }
    run = AgentRun(
        workspace_id=workspace.id,
        task_id=task.id,
        task_step_id=target.id,
        output={"structured_output": {"value": {"ok": True}}},
    )
    session.add(run)
    session.flush()
    TaskStepCompletionService(session, lambda *args: None).validate_step_output(
        run,
        AgentRunResult(
            final_output="",
            structured_output=AgentStructuredOutput(
                value=run.output["structured_output"]["value"],
                schema_name="workflow_result",
                validated=True,
            ),
        ),
    )
    run.output = {"structured_output": {"value": {"ok": "wrong"}}}
    with pytest.raises(ValueError, match="workflow step output"):
        TaskStepCompletionService(session, lambda *args: None).validate_step_output(
            run,
            AgentRunResult(
                final_output="",
                structured_output=AgentStructuredOutput(
                    value=run.output["structured_output"]["value"],
                    schema_name="workflow_result",
                    validated=True,
                ),
            ),
        )
