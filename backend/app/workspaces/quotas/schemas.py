from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    computed_field,
)

from backend.app.shared.contracts import TimestampedModel


class WorkspaceQuotaUpsertItem(BaseModel):
    quota_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    limit_value: int = Field(ge=0)
    unit: str = Field(default="count", min_length=1, max_length=32)


class WorkspaceQuotaUpsertRequest(BaseModel):
    quotas: list[WorkspaceQuotaUpsertItem] = Field(min_length=1, max_length=64)


class WorkspaceQuotaResponse(TimestampedModel):
    workspace_id: UUID
    quota_key: str
    limit_value: int
    reserved_value: int
    unit: str
    status: str

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def available_value(self) -> int:
        return max(self.limit_value - self.reserved_value, 0)

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def utilization(self) -> float:
        if self.limit_value <= 0:
            return 0.0
        return round(self.reserved_value / self.limit_value, 4)

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def saturated(self) -> bool:
        return self.limit_value > 0 and self.reserved_value >= self.limit_value

    @computed_field  # type: ignore[prop-decorator]  # Pydantic documented mypy limitation.
    @property
    def over_reserved(self) -> bool:
        return self.reserved_value > self.limit_value


class WorkspaceExecutionSlotReservationResponse(TimestampedModel):
    id: UUID
    workspace_project_id: UUID | None
    reservation_key: str
    task_id: UUID | None
    task_step_id: UUID | None
    agent_run_id: UUID | None
    resource_usage: dict[str, object]
    status: str
    expires_at: datetime | None
    released_at: datetime | None


class WorkspaceExecutionSlotSummaryResponse(BaseModel):
    workspace_id: UUID
    generated_at: datetime
    quotas: list[WorkspaceQuotaResponse]
    active_reservations: list[WorkspaceExecutionSlotReservationResponse]
    active_reservation_count: int
    reservation_usage: dict[str, int]
    over_reserved_quota_keys: list[str]
