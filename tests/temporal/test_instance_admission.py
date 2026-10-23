"""Full concurrency waits for the next Temporal admission poll."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

import opsmesh.workflows.engines.temporal.agent_activities as activities
from opsmesh.users.quotas.service import CapacityExceededError
from opsmesh.workflows.engines.temporal import execution_dispatch
from opsmesh.workflows.engines.temporal.agent_workflow import AgentInstanceWorkflow
from opsmesh.workflows.engines.temporal.workflow import WorkflowExecutionWorkflow


@pytest.mark.asyncio
async def test_capacity_exhaustion_leaves_run_queued(monkeypatch):
    run_id = uuid4()
    sessions = Mock()
    row = SimpleNamespace(status="queued")
    sessions.begin.return_value.__enter__ = Mock(return_value=Mock(get=Mock(return_value=row)))
    sessions.begin.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        activities.activity, "info", lambda: SimpleNamespace(workflow_id=f"instance:{run_id}")
    )
    monkeypatch.setattr(activities, "claim_run", Mock(side_effect=CapacityExceededError()))
    adapter = activities.InstanceActivities(
        sessions, Mock(), bindings=Mock(), history_root=Mock(), sandbox=Mock()
    )
    assert await adapter.claim({"execution_id": str(run_id)}) == {"waiting": True}
    assert row.status == "queued"
    assert sessions.begin.call_count == 1


def test_approval_dispatch_wakes_original_workflow_with_stable_identity(monkeypatch):
    run_id, approval_id = uuid4(), uuid4()
    session = Mock(
        get=Mock(return_value=SimpleNamespace(driver="temporal", approval_id=approval_id))
    )
    signal, start = Mock(), Mock()
    monkeypatch.setattr(execution_dispatch, "stage_signal", signal)
    monkeypatch.setattr(execution_dispatch, "stage_execution", start)
    execution_dispatch.dispatch_run(session, run_id)
    execution_dispatch.dispatch_run(session, run_id)
    assert signal.call_count == 2
    signal.assert_called_with(
        session,
        dispatch_id=approval_id,
        workflow_id=f"instance:{run_id}",
        signal_name="approval_decided",
        signal_args=(str(approval_id),),
    )
    start.assert_not_called()


@pytest.mark.asyncio
async def test_approval_notification_before_wait_is_not_lost(monkeypatch):
    owner = AgentInstanceWorkflow()
    approval_id = str(uuid4())
    await owner.approval_decided(approval_id, approval_id)

    async def ready(condition):
        assert condition()

    monkeypatch.setattr(
        "opsmesh.workflows.engines.temporal.agent_workflow.workflow.wait_condition", ready
    )
    await owner.wait_for_decision(approval_id)
    assert approval_id not in owner.decisions


@pytest.mark.asyncio
async def test_graph_wake_remains_effective_after_many_notifications():
    owner = WorkflowExecutionWorkflow()
    for index in range(8193):
        await owner.wake(str(index), str(index))
    assert owner.wake_generation == 8193
