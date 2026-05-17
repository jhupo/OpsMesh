from sqlalchemy.orm import Session

from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue


class WorkerJobHandler:
    def __init__(self, session: Session, queue: RedisQueue | None = None) -> None:
        self._session = session
        self._queue = queue

    def handle(self, job: JobPayload) -> None:
        match job.job_type:
            case JobType.AGENT_RUN:
                RunOrchestrationService(self._session, self._queue).run_fake_agent(job)
            case _:
                raise ValueError(f"Unsupported job type: {job.job_type}")

