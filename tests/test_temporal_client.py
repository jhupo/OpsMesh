"""Dispatch terminal operations safely without losing never-started signals."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import DispatchOperation, DispatchRequest, WorkflowSignal


def _request(operation):
    workspace_id, resource_id = uuid4(), uuid4()
    return DispatchRequest(
        dispatch_id=uuid4(),
        workspace_id=workspace_id,
        workflow_id=f"opsmesh:{workspace_id}:agent:{resource_id}",
        operation=operation,
        signal_name="approval" if operation == DispatchOperation.SIGNAL else None,
        signal=WorkflowSignal(resource_id=resource_id)
        if operation == DispatchOperation.SIGNAL
        else None,
    )


def test_cancellation_of_never_started_workflow_finishes_delivery() -> None:
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RPCError("Missing", RPCStatusCode.NOT_FOUND, b""))
    handle.cancel = AsyncMock()
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    asyncio.run(
        TemporalExecutionClient(client, task_queue="test").dispatch(
            _request(DispatchOperation.CANCEL)
        )
    )
    handle.cancel.assert_not_awaited()


def test_missing_workflow_signal_is_retained_for_outbox_retry() -> None:
    handle = MagicMock()
    handle.describe = AsyncMock(side_effect=RPCError("Missing", RPCStatusCode.NOT_FOUND, b""))
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    with pytest.raises(RPCError):
        asyncio.run(
            TemporalExecutionClient(client, task_queue="test").dispatch(
                _request(DispatchOperation.SIGNAL)
            )
        )


def test_workflow_completing_during_signal_finishes_delivery() -> None:
    handle = MagicMock()
    handle.describe = AsyncMock(
        side_effect=[
            SimpleNamespace(status=WorkflowExecutionStatus.RUNNING),
            SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED),
        ]
    )
    handle.signal = AsyncMock(side_effect=RPCError("Closed", RPCStatusCode.NOT_FOUND, b""))
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    asyncio.run(
        TemporalExecutionClient(client, task_queue="test").dispatch(
            _request(DispatchOperation.SIGNAL)
        )
    )
    assert handle.describe.await_count == 2
