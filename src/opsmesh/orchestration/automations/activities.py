from temporalio import activity

from opsmesh.orchestration.automations.execution_contracts import ReplyPreparation
from opsmesh.orchestration.automations.service import AutomationService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class AutomationActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.automation.dispatch")
    def dispatch(self, request: ExecutionRequest) -> str:
        with self._services.transaction() as session:
            return AutomationService(session).dispatch_event(
                request.workspace_id, request.resource_id
            )

    @activity.defn(name="opsmesh.automation.prepare_reply")
    def prepare_reply(self, request: ExecutionRequest) -> ReplyPreparation:
        with self._services.transaction() as session:
            return AutomationService(session).prepare_reply(
                request.workspace_id, request.resource_id
            )

    @activity.defn(name="opsmesh.automation.settle_reply")
    def settle_reply(self, request: ExecutionRequest) -> bool:
        with self._services.transaction() as session:
            return AutomationService(session).settle_reply(
                request.workspace_id, request.resource_id
            )
