from typing import NotRequired, TypedDict
from uuid import UUID


class MemberWorkload(TypedDict):
    team_member_id: UUID
    agent_profile_id: UUID
    reports_to_member_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    team_role: str
    department: str | None
    position_title: str | None
    responsibilities: list[str]
    status: str
    accepts_tasks: bool
    max_concurrent_tasks: int
    active_task_count: int
    workspace_active_task_count: int
    workspace_active_task_ids: list[UUID]
    active_step_count: int
    active_run_count: int
    active_run_phase_counts: dict[str, int]
    utilization: float
    overloaded: bool
    at_capacity: bool
    blocked_reasons: list[str]


class StaffingGap(TypedDict):
    required_role: str | None
    required_skills: list[str]
    step_count: int
    task_count: int
    task_ids: list[UUID]
    task_step_ids: list[UUID]
    matching_member_count: int
    recommended_action: str


class SpecialistReassignment(TypedDict):
    task_id: UUID
    task_step_id: UUID
    step_status: str
    required_role: str | None
    required_skills: list[str]
    current_agent_profile_id: UUID | None
    replacement_agent_profile_id: UUID
    replacement_agent_name: str
    replacement_agent_role: str
    replacement_team_role: str
    reason: str


class SummaryAction(TypedDict):
    action: str
    count: int
    task_ids: list[UUID]


class ExecutionBottleneck(TypedDict):
    code: str
    severity: str
    count: int
    task_ids: list[UUID]
    recommended_action: str


class OperatorIntervention(TypedDict):
    action: str
    category: str
    severity: str
    priority: int
    count: int
    task_ids: list[UUID]
    task_step_ids: list[UUID]
    automation: str
    operator_action: str | None
    api_route: str
    payload_template: dict[str, object]
    reason_codes: list[str]
    agent_profile_id: NotRequired[UUID]
    current_agent_profile_id: NotRequired[UUID | None]
    replacement_agent: NotRequired[dict[str, object]]
