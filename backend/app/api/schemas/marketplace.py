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
    current_talent_listing_id: UUID | None
    source_agent_profile_id: UUID | None
    installed_agent_profile_id: UUID
    hired_by_user_id: UUID | None
    installed_version: int
    pinned_version: bool
    status: str
    agent: AgentProfileResponse


class TalentRecommendationRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2_000)
    team_type: str = Field(default="general", max_length=80)
    required_roles: list[str] = Field(default_factory=list, max_length=12)
    skill_tags: list[str] = Field(default_factory=list, max_length=24)
    capability_tags: list[str] = Field(default_factory=list, max_length=24)
    team_id: UUID | None = None
    max_candidates_per_role: int = Field(default=3, ge=1, le=10)


class TalentCandidateRecommendation(BaseModel):
    listing: TalentListingResponse
    score: float
    matched_reasons: list[str]
    missing_tags: list[str]


class RoleRecommendation(BaseModel):
    role: str
    team_role: str
    priority: int = Field(ge=1)
    reason: str
    candidates: list[TalentCandidateRecommendation]


class TalentRecommendationResponse(BaseModel):
    objective: str
    team_type: str
    recommended_roles: list[RoleRecommendation]
    existing_team_roles: list[str]
    uncovered_roles: list[str]


class TalentUpgradeStatusResponse(BaseModel):
    install: WorkspaceAgentInstallResponse
    latest_listing: TalentListingResponse | None
    has_update: bool
    pinned_version: bool


class TalentInstallPinRequest(BaseModel):
    pinned_version: bool


class TalentInstallUpgradeRequest(BaseModel):
    target_listing_id: UUID | None = None
    keep_pinned: bool = True
