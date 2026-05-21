from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from backend.app.api.schemas.common import TimestampedModel


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    settings: dict[str, object] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = Field(default=None, max_length=32)
    settings: dict[str, object] | None = None


class WorkspaceResponse(TimestampedModel):
    owner_user_id: UUID
    name: str
    slug: str
    status: str
    settings: dict[str, object]


class WorkspaceMemberResponse(TimestampedModel):
    workspace_id: UUID
    user_id: UUID
    role: str
    status: str


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

    @computed_field
    @property
    def available_value(self) -> int:
        return max(self.limit_value - self.reserved_value, 0)

    @computed_field
    @property
    def utilization(self) -> float:
        if self.limit_value <= 0:
            return 0.0
        return round(self.reserved_value / self.limit_value, 4)

    @computed_field
    @property
    def saturated(self) -> bool:
        return self.limit_value > 0 and self.reserved_value >= self.limit_value
