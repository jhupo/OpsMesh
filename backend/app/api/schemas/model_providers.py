from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

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
    base_url: str | None
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
