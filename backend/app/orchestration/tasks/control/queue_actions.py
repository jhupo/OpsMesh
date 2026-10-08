from typing import TypedDict
from uuid import UUID


class TeamQueueActionPlan(TypedDict):
    team_id: UUID
    action: str
    automation: str
    api_route: str
    task_ids: list[UUID]
    task_step_ids: list[UUID]
    count: int
    reason: str


def append_unique_uuid(values: list[UUID], value: UUID) -> None:
    if value not in values:
        values.append(value)
        values.sort(key=str)
