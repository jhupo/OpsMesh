from typing import TypedDict
from uuid import UUID


class TaskOperatorActionResult(TypedDict):
    changed_step_ids: list[UUID]
    created_step_ids: list[UUID]
    warnings: list[str]
    details: dict[str, object]
