from datetime import datetime

from pydantic import BaseModel, ConfigDict

from opsmesh.runtime.temporal.contracts import ExecutionRequest


class ScheduleOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ExecutionRequest
    occurrence_id: str
    started_at: datetime
