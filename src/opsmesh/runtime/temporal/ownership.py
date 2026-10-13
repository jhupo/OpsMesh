"""Reject host-managed maintenance when native queues have unmanaged pollers."""

from datetime import timedelta

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from opsmesh.shared.config import Settings


async def require_managed_worker_ownership(client: Client, settings: Settings) -> None:
    identity = settings.temporal_worker_identity
    if identity is None:
        raise ValueError("Host maintenance requires an explicit Temporal Worker identity")
    for queue in (
        settings.temporal_task_queue,
        settings.temporal_task_queue + "-archives",
    ):
        for kind in (
            TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
            TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
        ):
            response = await client.workflow_service.describe_task_queue(
                DescribeTaskQueueRequest(
                    namespace=client.namespace,
                    task_queue=TaskQueue(name=queue),
                    task_queue_type=kind,
                ),
                timeout=timedelta(seconds=10),
            )
            if any(poller.identity != identity for poller in response.pollers):
                raise ValueError(
                    "Unmanaged Temporal Workers are present; stop the fleet before host maintenance"
                )
