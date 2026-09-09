from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, computed_field, field_serializer

from backend.app.api.schemas.common import ORMModel
from backend.app.model_providers.base_url import model_provider_base_url_host
from backend.app.model_providers.capabilities import resolve_model_capability
from backend.app.model_providers.metadata import sanitize_budget_metadata
from backend.app.model_providers.model_api import model_api_for_provider
from backend.app.secrets.service import hosted_secret_metadata


class ModelProviderCredentialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(default="openai", min_length=1, max_length=80)
    api_key: str = Field(min_length=1, max_length=4096)
    default_model: str = Field(default="gpt-4.1", min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    model_api: str | None = Field(default=None, min_length=1, max_length=80)
    is_default: bool = False
    budget_metadata: dict[str, object] = Field(default_factory=dict)


class ModelProviderCredentialUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    default_model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    model_api: str | None = Field(default=None, min_length=1, max_length=80)
    is_default: bool | None = None
    budget_metadata: dict[str, object] | None = None


class ModelProviderCredentialRotateKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


class ModelProviderHealthCheckRequest(BaseModel):
    probes: list[str] = Field(default_factory=lambda: ["models", "inference"], max_length=2)
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class ModelProviderCredentialResponse(ORMModel):
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID | None
    name: str
    provider: str
    base_url: str | None = Field(exclude=True, repr=False)
    default_model: str
    api_key_fingerprint: str
    encryption_key_id: str
    is_default: bool
    status: str
    health_status: str
    failure_count: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_code: str | None
    last_failure_message: str | None
    budget_metadata: dict[str, object] = Field(default_factory=dict)
    scheduled_health_check: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_serializer("budget_metadata")
    def serialize_budget_metadata(self, value: dict[str, object]) -> dict[str, object]:
        return sanitize_budget_metadata(value)

    @computed_field
    @property
    def base_url_configured(self) -> bool:
        return bool(self.base_url)

    @computed_field
    @property
    def base_url_host(self) -> str | None:
        return model_provider_base_url_host(self.base_url)

    @computed_field
    @property
    def model_api(self) -> str | None:
        return model_api_for_provider(self.provider, self.budget_metadata)

    @computed_field
    @property
    def model_capability(self) -> dict[str, object] | None:
        capability = resolve_model_capability(self.provider, self.default_model)
        return capability.as_dict() if capability is not None else None

    @computed_field
    @property
    def last_health_check_at(self) -> datetime | None:
        timestamps = [
            value
            for value in (self.last_success_at, self.last_failure_at)
            if value is not None
        ]
        return max(timestamps) if timestamps else None

    @computed_field
    @property
    def secret_metadata(self) -> dict[str, object]:
        return hosted_secret_metadata(
            provider="hosted",
            encryption_key_id=self.encryption_key_id,
            secret_fingerprint=self.api_key_fingerprint,
        ).to_api_dict()


class ModelProviderHealthCheckItemResponse(BaseModel):
    name: str
    status: str
    code: str | None = None
    message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ModelProviderHealthCheckResponse(BaseModel):
    credential: ModelProviderCredentialResponse
    status: str
    checks: list[ModelProviderHealthCheckItemResponse]


class ModelProviderUsageAuditResponse(BaseModel):
    id: UUID
    action: str
    run_id: str | None
    task_id: str | None
    task_step_id: str | None
    agent_profile_id: str | None
    provider: str | None
    model: str | None
    model_api: str | None
    credential_id: str | None
    fallback_selected: bool | None
    reason: dict[str, object] | None = None
    failed_provider: dict[str, object] | None = None
    created_at: datetime


class ModelCapabilityResponse(BaseModel):
    provider: str
    model: str
    display_name: str
    capabilities: list[str]
    model_apis: list[str] = Field(default_factory=list)
    default_model_api: str | None = None
    supports_tools: bool
    supports_vision: bool
    supports_json_mode: bool
    supports_streaming: bool
    context_window_tokens: int | None = None
    notes: str | None = None


class AgentRuntimeCapabilityFeatureResponse(BaseModel):
    name: str
    supported: bool
    reason: str | None = None


class AgentRuntimeAdapterCapabilityResponse(BaseModel):
    provider: str
    adapter: str
    features: list[AgentRuntimeCapabilityFeatureResponse]
    limits: dict[str, object] = Field(default_factory=dict)
