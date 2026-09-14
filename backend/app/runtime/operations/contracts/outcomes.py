from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RunFailureReasonResponse(BaseModel):
    code: str
    count: int


class RunOutcomeWindowResponse(BaseModel):
    window_seconds: int
    total_runs: int
    completed_runs: int
    failed_runs: int
    cancelled_runs: int
    failure_rate: float
    failure_reasons: list[RunFailureReasonResponse]


class ApprovalBacklogResponse(BaseModel):
    pending: int
    high_risk_pending: int
    oldest_pending_age_seconds: int | None
    pending_by_type: dict[str, int]


class OperationsOutcomesResponse(BaseModel):
    generated_at: datetime
    runs: RunOutcomeWindowResponse
    approvals: ApprovalBacklogResponse


class McpJobStatusBucketResponse(BaseModel):
    status: str
    count: int


class McpJobToolBucketResponse(BaseModel):
    tool_name: str
    queued: int
    claimed: int
    completed: int
    failed: int
    total: int


class OperationsMcpJobsResponse(BaseModel):
    generated_at: datetime
    total: int
    queued: int
    claimed: int
    completed: int
    failed: int
    oldest_queued_age_seconds: int | None
    statuses: list[McpJobStatusBucketResponse]
    tools: list[McpJobToolBucketResponse]
