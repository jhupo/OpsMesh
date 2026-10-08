
from pydantic import (
    BaseModel,
)

from backend.app.runtime.queues.contracts import JobPayload


class AdminDeadLetterJobsResponse(BaseModel):
    items: list[JobPayload]
    total: int


class AdminRequeueDeadLetterResponse(BaseModel):
    requeued: bool
    job: JobPayload | None = None
