from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SchedulerBacklogResponse(BaseModel):
    queued_steps: int
    running_steps: int
    waiting_approval_tasks: int
    blocked_steps: int
    active_runs: int
    oldest_queued_age_seconds: int | None
    highest_priority: int | None


class SchedulerPriorityBucketResponse(BaseModel):
    priority: int
    queued_steps: int
    running_steps: int
    blocked_steps: int


class SchedulerBlockedReasonResponse(BaseModel):
    reason: str
    code: str
    message: str
    resource_key: str | None = None
    count: int


class BlockedStepExplanationResponse(BaseModel):
    task_step_id: UUID
    task_id: UUID
    task_title: str
    step_title: str
    status: str
    reason: str
    code: str
    message: str
    resource_key: str | None = None
    runtime_space_id: UUID | None = None
    blocked_resource_keys: list[str] = Field(default_factory=list)
    priority_score: int | None = None
    created_at: datetime
    updated_at: datetime


class BlockedStepUnblockRequest(BaseModel):
    code: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=240)
    runtime_space_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=500)


class BlockedStepUnblockResponse(BaseModel):
    workspace_id: UUID
    unblocked_steps: int


class SchedulerPauseRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class SchedulerPolicyResponse(BaseModel):
    paused: bool = False
    pause_reason: str | None = None
    max_active_runs: int | None
    max_running_tasks: int | None
    max_runs_to_start_per_tick: int | None
    max_steps_per_task_per_tick: int | None
    starvation_boost_after_seconds: int | None
    resource_limits: dict[str, float]


class OperationsSchedulerResponse(BaseModel):
    generated_at: datetime
    backlog: SchedulerBacklogResponse
    priority_buckets: list[SchedulerPriorityBucketResponse]
    blocked_reasons: list[SchedulerBlockedReasonResponse]
    policy: SchedulerPolicyResponse


class SchedulerControlResponse(BaseModel):
    workspace_id: UUID
    paused: bool
    pause_reason: str | None = None
    cleared_blocked_steps: int = 0
    policy: SchedulerPolicyResponse
