from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from opsmesh.runtime.temporal.contracts import ExecutionKind

ACTIVE_STATUS = "active"

COMPLETED_STATUS = "completed"

PAUSED_STATUS = "paused"

EXECUTE_ACTION = "execute"

RECORD_DUE_ACTION = "record_due_action"


@dataclass(frozen=True)
class ScheduledJobCreateCommand:
    name: str
    schedule_type: str
    schedule_config: dict[str, object]
    action_type: str
    execution_kind: ExecutionKind | None
    resource_id: UUID | None
    parameters: dict[str, JsonValue]
    metadata: dict[str, object]
