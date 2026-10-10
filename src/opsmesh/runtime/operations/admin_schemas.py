
from pydantic import (
    BaseModel,
)


class AdminOperationsSummaryResponse(BaseModel):
    queue: dict[str, object]
    workers: dict[str, object]
    runtime_spaces: dict[str, object]
    approvals: dict[str, object]
    failures: dict[str, object]
