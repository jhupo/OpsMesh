from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import ORMModel, TimestampedModel


class TaskCreateRequest(BaseModel):
    agent_team_id: UUID | None = None
    runtime_space_id: UUID | None = None
    domain_type: str = Field(default="general", max_length=80)
    title: str = Field(min_length=1, max_length=240)
    description: str = ""
    priority: int = 0
    input: dict[str, object] = Field(default_factory=dict)
    generic_state: dict[str, object] = Field(default_factory=dict)
    domain_state: dict[str, object] = Field(default_factory=dict)


class TaskPlanRetryRequest(BaseModel):
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = False
    enqueue: bool = True


class TaskPlanRegenerateRequest(BaseModel):
    input: dict[str, object] | None = None
    refresh_team_snapshot: bool = True
    enqueue: bool = False


class TaskCorrectionRequest(BaseModel):
    target_type: str = Field(pattern="^(task|step|agent|artifact|final_output)$")
    mode: str = Field(pattern="^(revise|regenerate|add_missing_work|replace_artifact|stop_work)$")
    instruction: str = Field(min_length=1, max_length=4_000)
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskResponse(TimestampedModel):
    workspace_id: UUID
    created_by_user_id: UUID | None
    created_by_agent_run_id: UUID | None
    agent_team_id: UUID | None
    runtime_space_id: UUID | None
    domain_type: str
    title: str
    description: str
    status: str
    priority: int
    input: dict[str, object]
    generic_state: dict[str, object]
    domain_state: dict[str, object]
    team_snapshot: dict[str, object] | None
    project_plan: dict[str, object] | None
    final_output: dict[str, object] | None
    completed_at: datetime | None


class TaskMessageResponse(TimestampedModel):
    workspace_id: UUID
    task_id: UUID
    task_step_id: UUID | None
    agent_run_id: UUID | None
    agent_profile_id: UUID | None
    message_type: str
    sequence: int
    body: str
    payload: dict[str, object]


class TaskPlanningAttemptResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    task_id: UUID
    planner_agent_profile_id: UUID | None
    attempt_number: int
    status: str
    strategy: str
    input_snapshot: dict[str, object]
    output_snapshot: dict[str, object] | None
    validation_errors: list[str]
    retry_count: int
    created_at: datetime
    completed_at: datetime | None


class TaskCorrectionResponse(BaseModel):
    task_id: UUID
    mode: str
    target_type: str
    created_step_id: UUID | None
    message_id: UUID
    status: str


class TaskObservationCard(BaseModel):
    card_type: str
    title: str
    status: str
    data: dict[str, object]


class TaskObservationSection(BaseModel):
    key: str
    title: str
    cards: list[TaskObservationCard]


class TaskObservationResponse(BaseModel):
    workspace_id: UUID
    task_id: UUID
    view_type: str
    generated_at: datetime
    summary: dict[str, object]
    sections: list[TaskObservationSection]
