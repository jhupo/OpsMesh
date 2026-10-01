from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
)


class AdminPluginGovernanceRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1_000)


class AdminPluginGovernanceResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    plugin_key: str
    status: str
    platform_blocked: bool
    generation: int
    current_version: str


class AdminPluginPublisherTrustResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    key_id: str
    plugin_key: str
    status: str
