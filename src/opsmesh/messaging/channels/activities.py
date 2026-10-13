"""Bounded delivery Activities; asynchronous receipts are waited on by Workflow."""

from temporalio import activity

from opsmesh.messaging.channels.delivery import ChannelDeliveryService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class ChannelDeliveryActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.channel.send")
    def send(self, request: ExecutionRequest) -> str:
        with self._services.transaction() as session:
            return ChannelDeliveryService(session, self._services.secrets()).send(
                request.workspace_id,
                request.resource_id,
            )

    @activity.defn(name="opsmesh.channel.interrupted")
    def interrupted(self, request: ExecutionRequest) -> str:
        with self._services.transaction() as session:
            return ChannelDeliveryService(session, self._services.secrets()).interrupted(
                request.workspace_id,
                request.resource_id,
            )
