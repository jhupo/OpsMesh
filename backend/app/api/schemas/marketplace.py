from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.api.schemas.agents import AgentProfileResponse
from backend.app.api.schemas.common import TimestampedModel


class TalentListingCreateRequest(BaseModel):
    agent_profile_id: UUID
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=2_000)
    skill_tags: list[str] = Field(default_factory=list)
    capability_tags: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    default_team_role: str | None = Field(default=None, max_length=80)
    risk_level: str = Field(default="low", max_length=32)
    metadata: dict[str, object] = Field(default_factory=dict)


class TalentListingResponse(TimestampedModel):
    owner_user_id: UUID
    source_workspace_id: UUID
    source_agent_profile_id: UUID
    title: str
    role: str
    summary: str
    skill_tags: list[str]
    capability_tags: list[str]
    required_tools: list[str]
    default_team_role: str | None
    risk_level: str
    listing_metadata: dict[str, object]
    version: int
    status: str


class HireTalentRequest(BaseModel):
    agent_name: str | None = Field(default=None, max_length=160)
    team_id: UUID | None = None
    team_role: str | None = Field(default=None, max_length=80)
    order_index: int = Field(default=0, ge=0)


class WorkspaceAgentInstallResponse(TimestampedModel):
    workspace_id: UUID
    talent_listing_id: UUID
    source_agent_profile_id: UUID | None
    installed_agent_profile_id: UUID
    hired_by_user_id: UUID | None
    status: str
    agent: AgentProfileResponse
