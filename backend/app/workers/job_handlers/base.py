from typing import Protocol

from backend.app.workers.jobs import JobPayload


class WorkerJobTypeHandler(Protocol):
    def handle(self, job: JobPayload) -> None: ...
