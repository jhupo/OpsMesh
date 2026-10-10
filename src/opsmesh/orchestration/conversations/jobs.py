from opsmesh.orchestration.conversations.advancement import ConversationAdvanceService
from opsmesh.runtime.queues.context import WorkerJobHandlerContext
from opsmesh.runtime.queues.contracts import JobPayload


class ConversationAdvanceJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self.context = context

    def handle(self, job: JobPayload) -> None:
        ConversationAdvanceService(self.context.session).advance(job.workspace_id, job.resource_id)
