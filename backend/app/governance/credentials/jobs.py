
from backend.app.governance.credentials.service import HostedSecretReencryptService
from backend.app.runtime.queues.context import WorkerJobHandlerContext
from backend.app.runtime.queues.contracts import JobPayload


class SecretReencryptJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        scope = job.routing.get("scope")
        workspace_id = None if scope == "global" else job.workspace_id
        HostedSecretReencryptService(
            self._context.session,
            self._context.secret_service(context="secret reencryption"),
        ).reencrypt(workspace_id=workspace_id)
        self._context.session.commit()
