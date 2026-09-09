from datetime import datetime
from typing import TypedDict
from uuid import UUID

from backend.app.tasks.models import TaskStep


class ManagerSteps(TypedDict):
    planning: TaskStep | None
    summaries: list[TaskStep]
    revision_reviews: list[TaskStep]
    all: list[TaskStep]
    ids: set[UUID]


class ManagerAgent(TypedDict):
    id: UUID
    name: str
    role: str
    status: str


class ManagerInfo(TypedDict):
    agent_profile_id: UUID | None
    agent: ManagerAgent | None
    has_manager: bool
    planning_step_id: UUID | None
    summary_step_ids: list[UUID | None]
    revision_review_step_ids: list[UUID | None]


class ManagerSummary(TypedDict):
    status: str
    total_steps: int
    specialist_steps: int
    manager_steps: int
    acceptance_decisions: int
    follow_up_cycles: int
    step_status_counts: dict[str, int]
    decision_counts: dict[str, int]


class FollowUpCycle(TypedDict):
    message_id: UUID
    revision_cycle: object
    decision: object
    follow_up_step_ids: list[UUID | None]
    follow_up_work_package_ids: list[str | None]
    review_step_ids: list[UUID | None]
    status: str
    blocked_reasons: list[str]
    metadata: dict[str, object]
    created_at: datetime


class ManagerDiagnostics(TypedDict):
    workspace_id: UUID
    task_id: UUID
    generated_at: datetime
    manager: ManagerInfo
    summary: ManagerSummary
    handoff_chain: list[dict[str, object]]
    acceptance_decisions: list[dict[str, object]]
    follow_up_cycles: list[FollowUpCycle]
    blocked_reasons: list[str]


class ManagerQueueItem(TypedDict):
    task_id: UUID
    team_id: UUID | None
    title: str
    status: str
    priority: int
    domain_type: str | None
    manager_agent_profile_id: UUID | None
    manager_agent_name: str | None
    manager_status: str
    summary_status: str
    pending_phase: str
    needs_attention: bool
    blocked_reasons: list[str]
    recommended_actions: list[str]
    acceptance_decisions: int
    follow_up_cycles: int
    step_status_counts: dict[str, int]
    last_activity_at: datetime
