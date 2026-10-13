from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.runtime.temporal.client import WORKFLOW_NAMES
from opsmesh.runtime.temporal.contracts import (
    DispatchOperation,
    DispatchRequest,
    ExecutionKind,
    ExecutionRequest,
)


def test_execution_request_rejects_unrecognized_parameters() -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest(
            workspace_id=uuid4(),
            resource_id=uuid4(),
            kind=ExecutionKind.MEMORY_INDEX,
            parameters={"prompt": "must remain outside the durable reference"},
        )


def test_every_execution_kind_has_a_workflow_registration() -> None:
    from temporalio import workflow

    from opsmesh.runtime.temporal.worker import REGISTERED_WORKFLOWS

    names = {workflow._Definition.must_from_class(item).name for item in REGISTERED_WORKFLOWS}
    assert set(WORKFLOW_NAMES) == set(ExecutionKind)
    assert (
        set(WORKFLOW_NAMES.values())
        | {
            "opsmesh.platform_maintenance",
            "opsmesh.workspace_maintenance",
        }
        == names
    )


def test_start_dispatch_uses_workspace_scoped_stable_workflow_id() -> None:
    workspace_id = uuid4()
    execution = ExecutionRequest(
        workspace_id=workspace_id,
        resource_id=uuid4(),
        kind=ExecutionKind.AUDIT_INTEGRITY,
    )
    dispatch = DispatchRequest(
        dispatch_id=uuid4(),
        workspace_id=workspace_id,
        workflow_id=execution.workflow_id,
        operation=DispatchOperation.START,
        execution=execution,
    )
    assert dispatch.execution == execution
    assert dispatch.workflow_id.startswith(f"opsmesh:{workspace_id}:")


def test_start_dispatch_rejects_cross_workspace_workflow() -> None:
    workspace_id = uuid4()
    execution = ExecutionRequest(
        workspace_id=workspace_id,
        resource_id=uuid4(),
        kind=ExecutionKind.AUDIT_INTEGRITY,
    )
    with pytest.raises(ValidationError):
        DispatchRequest(
            dispatch_id=uuid4(),
            workspace_id=uuid4(),
            workflow_id=execution.workflow_id,
            operation=DispatchOperation.START,
            execution=execution,
        )
