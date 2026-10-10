from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_serializer,
)

from opsmesh.shared.contracts import ORMModel, TimestampedModel
from opsmesh.shared.security.redaction import redact_sensitive_payload


class AdminProjectResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    slug: str
    description: str
    input_path: str
    work_path: str
    output_path: str
    configuration: dict[str, object]
    configuration_version: int
    status: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("configuration")
    def _serialize_configuration(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class AdminProjectStatusUpdateRequest(BaseModel):
    status: Literal["active", "archived", "disabled"]
    reason: str = "Updated by platform admin"


class AdminProjectQuotaUpsertItem(BaseModel):
    quota_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    limit_value: int = Field(ge=0)
    unit: str = Field(default="count", min_length=1, max_length=32)


class AdminProjectQuotaUpsertRequest(BaseModel):
    quotas: list[AdminProjectQuotaUpsertItem] = Field(min_length=1, max_length=64)


class AdminProjectQuotaResponse(TimestampedModel):
    workspace_id: UUID
    project_id: UUID
    quota_key: str
    limit_value: int
    reserved_value: int
    unit: str
    status: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available_value(self) -> int:
        return max(self.limit_value - self.reserved_value, 0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def utilization(self) -> float:
        if self.limit_value <= 0:
            return 0.0
        return round(self.reserved_value / self.limit_value, 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def saturated(self) -> bool:
        return self.limit_value > 0 and self.reserved_value >= self.limit_value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def over_reserved(self) -> bool:
        return self.reserved_value > self.limit_value
