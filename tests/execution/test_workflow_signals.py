"""Execution receivers honor the dispatch identity in at-least-once signals."""

import asyncio
from uuid import uuid4

import pytest

from opsmesh.orchestration.runs.agent_workflow import ExecutionRunWorkflow
from opsmesh.orchestration.runtime.workflow import ExecutionMCPWorkflow, ExecutionRuntimeWorkflow


def test_duplicate_wake_does_not_wake_a_later_admission_wait():
    receiver = ExecutionRunWorkflow()
    receiver.wake("wake-one")
    assert receiver.woken
    receiver.woken = False  # The first admission attempt consumed this wake.
    receiver.wake("wake-one")
    assert not receiver.woken
    receiver.wake("wake-two")
    assert receiver.woken
    approval = uuid4()
    receiver.approval("decision-one", approval)
    receiver.approval("decision-one", approval)
    assert receiver.decisions == {approval}
    receiver.cancel("cancel-one")
    receiver.cancel("cancel-one")
    assert receiver.cancelled


@pytest.mark.parametrize("workflow_type", [ExecutionRuntimeWorkflow, ExecutionMCPWorkflow])
def test_duplicate_cancel_does_not_interrupt_activity_cleanup_again(workflow_type):
    async def verify():
        receiver = workflow_type()
        receiver.operation = asyncio.create_task(asyncio.sleep(1))
        receiver.cancel("cancel-one")
        receiver.cancel("cancel-one")
        assert receiver.operation.cancelling() == 1
        with pytest.raises(asyncio.CancelledError):
            await receiver.operation

    asyncio.run(verify())
