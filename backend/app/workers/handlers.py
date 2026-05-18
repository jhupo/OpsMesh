from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunner
from backend.app.api.services.exports import WorkspaceExportService
from backend.app.core.config import Settings
from backend.app.files.storage import LocalStorage
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


class WorkerJobHandler:
    def __init__(
        self,
        session: Session,
        queue: RedisQueue | None = None,
        agent_runner: AgentRunner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._queue = queue
        self._agent_runner = agent_runner
        self._settings = settings

    def handle(self, job: JobPayload) -> None:
        match job.job_type:
            case JobType.AGENT_RUN:
                RunOrchestrationService(
                    self._session,
                    self._queue,
                    self._agent_runner,
                    self._settings,
                ).run_fake_agent(job)
            case JobType.WORKSPACE_ARCHIVE_EXPORT:
                if self._settings is None:
                    raise ValueError("Worker settings are required for workspace archive export")
                WorkspaceExportService(self._session).run_archive_export_job(
                    job=job,
                    storage=LocalStorage(self._settings.storage_root),
                )
            case _:
                raise ValueError(f"Unsupported job type: {job.job_type}")
