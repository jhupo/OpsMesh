from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, computed_field

from backend.app.api.schemas.common import ORMModel


class ModelProviderCredentialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(default="openai", min_length=1, max_length=80)
    api_key: str = Field(min_length=1, max_length=4096)
    default_model: str = Field(default="gpt-4.1", min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    is_default: bool = False


class ModelProviderCredentialUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    default_model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: HttpUrl | None = None
    is_default: bool | None = None


class ModelProviderCredentialRotateKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


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
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_code: str | None
    last_failure_message: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def base_url_configured(self) -> bool:
        return bool(self.base_url)

    @computed_field
    @property
    def base_url_host(self) -> str | None:
        if not self.base_url:
            return None
        parsed = urlparse(self.base_url)
        return parsed.netloc or None


class ModelProviderUsageAuditResponse(BaseModel):
    id: UUID
    action: str
    run_id: str | None
    task_id: str | None
    task_step_id: str | None
    agent_profile_id: str | None
    model: str | None
    credential_id: str | None
    fallback_selected: bool | None
    reason: dict[str, object] | None = None
    failed_provider: dict[str, object] | None = None
    created_at: datetime
