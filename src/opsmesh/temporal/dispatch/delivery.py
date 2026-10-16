"""Deliver committed workflow commands using caller-selected native SDK policies."""

from datetime import timedelta

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from opsmesh.temporal.dispatch.contracts import (
    DispatchOperation,
    DispatchRequest,
)

DEFAULT_RPC_TIMEOUT = timedelta(seconds=10)


async def dispatch_workflow(
    client: Client,
    request: DispatchRequest,
    *,
    task_queue: str,
    rpc_timeout: timedelta = DEFAULT_RPC_TIMEOUT,
) -> None:
    await _deliver(client, request, task_queue=task_queue, rpc_timeout=rpc_timeout)


async def _deliver(
    client: Client, request: DispatchRequest, *, task_queue: str, rpc_timeout: timedelta
) -> None:
    if request.operation == DispatchOperation.START:
        execution, options = request.execution, request.start_options
        if execution is None or options is None or request.workflow_name is None:
            raise ValueError("Start dispatch has no workflow reference or options")
        try:
            await client.start_workflow(
                request.workflow_name,
                execution,
                id=request.workflow_id,
                task_queue=task_queue,
                id_reuse_policy=options.id_reuse_policy,
                id_conflict_policy=options.id_conflict_policy,
                start_signal=options.start_signal,
                start_signal_args=options.start_signal_args,
                rpc_timeout=rpc_timeout,
            )
        except WorkflowAlreadyStartedError:
            if not options.ignore_existing:
                raise
        return
    handle = client.get_workflow_handle(request.workflow_id)
    if request.operation == DispatchOperation.SIGNAL:
        if request.signal_name is None or request.signal_args is None:
            raise ValueError("Signal dispatch has no decision reference")
        description = await handle.describe(rpc_timeout=rpc_timeout)
        if description.status == WorkflowExecutionStatus.RUNNING:
            try:
                await handle.signal(
                    request.signal_name,
                    # The dispatch identity leads the payload so the receiver can
                    # deduplicate an at-least-once redelivery of this command.
                    args=(str(request.dispatch_id), *request.signal_args),
                    rpc_timeout=rpc_timeout,
                )
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                current = await handle.describe(rpc_timeout=rpc_timeout)
                if current.status == WorkflowExecutionStatus.RUNNING:
                    raise
        return
    try:
        description = await handle.describe(rpc_timeout=rpc_timeout)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return
        raise
    if description.status == WorkflowExecutionStatus.RUNNING:
        await handle.cancel(rpc_timeout=rpc_timeout)
