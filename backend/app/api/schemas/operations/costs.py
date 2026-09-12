from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.common import ORMModel


class ModelPricingRuleCreateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    currency: str = Field(default="USD", pattern="^[A-Za-z]{3}$")
    input_rate_per_million: Decimal = Field(ge=0)
    output_rate_per_million: Decimal = Field(ge=0)
    cached_input_rate_per_million: Decimal | None = Field(default=None, ge=0)
    request_rate: Decimal = Field(default=Decimal("0"), ge=0)
    effective_from: datetime
    effective_to: datetime | None = None
    source: str = Field(default="operator", min_length=1, max_length=500)


class ModelPricingRuleResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    provider: str
    model: str
    version: str
    currency: str
    input_rate_per_million: Decimal
    output_rate_per_million: Decimal
    cached_input_rate_per_million: Decimal | None
    request_rate: Decimal
    effective_from: datetime
    effective_to: datetime | None
    status: str
    source: str
    created_at: datetime
    updated_at: datetime


class WorkspaceCostBudgetRequest(BaseModel):
    currency: str = Field(default="USD", pattern="^[A-Za-z]{3}$")
    monthly_limit: Decimal = Field(gt=0)
    warning_ratio: Decimal = Field(default=Decimal("0.8"), gt=0, le=1)
    enforcement: str = Field(default="warn", pattern="^(warn|block)$")
    enabled: bool = True


class WorkspaceCostBudgetResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    currency: str
    monthly_limit: Decimal
    warning_ratio: Decimal
    enforcement: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ModelUsageRecordResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    agent_run_id: UUID
    task_id: UUID | None
    agent_profile_id: UUID | None
    pricing_rule_id: UUID | None
    provider: str
    model: str
    model_api: str | None
    pricing_version: str | None
    currency: str | None
    metering_status: str
    job_attempt: int
    request_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    total_tokens: int
    input_cost: Decimal | None
    output_cost: Decimal | None
    cached_input_cost: Decimal | None
    request_cost: Decimal | None
    total_cost: Decimal | None
    trace_id: str | None
    occurred_at: datetime


class CostTotalsResponse(BaseModel):
    records: int
    priced_records: int
    unpriced_records: int
    request_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    total_tokens: int
    total_cost: Decimal


class CostGroupResponse(CostTotalsResponse):
    key: str


class CostBudgetStatusResponse(BaseModel):
    state: str
    currency: str
    spent: Decimal
    monthly_limit: Decimal | None
    warning_ratio: Decimal | None
    utilization_ratio: Decimal | None
    enforcement: str | None
    unpriced_records: int
    period_start: datetime
    period_end: datetime


class CostSummaryResponse(BaseModel):
    start_at: datetime
    end_at: datetime
    currency: str
    group_by: str
    totals: CostTotalsResponse
    groups: list[CostGroupResponse]
    budget: CostBudgetStatusResponse
