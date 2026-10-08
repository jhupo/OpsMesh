from typing import Protocol

from backend.app.runtime.queues.contracts import JobPayload


class WorkerJobTypeHandler(Protocol):
    def handle(self, job: JobPayload) -> None: ...


class WorkerFailureHandler(Protocol):
    def __call__(self, job: JobPayload, *, status: str, error: BaseException) -> None: ...
