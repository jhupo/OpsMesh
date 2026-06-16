from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from backend.app.api.schemas.common import TimestampedModel
from backend.app.secrets.service import (
    external_vault_reference_metadata,
    hosted_secret_metadata,
    vault_reference_kind,
)


class McpCredentialReferenceCreateRequest(BaseModel):
    mcp_server_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=80)
    external_ref: str = Field(default="", max_length=512)
    secret_payload: dict[str, object] | None = None
    scopes: list[str] = Field(default_factory=list)


class McpCredentialReferenceResponse(TimestampedModel):
    workspace_id: UUID
    mcp_server_id: UUID | None
    name: str
    provider: str
    external_ref: str = Field(exclude=True, repr=False)
    secret_fingerprint: str | None
    encryption_key_id: str | None
    scopes: list[str]
    status: str

    @computed_field
    @property
    def external_ref_configured(self) -> bool:
        raw_value = getattr(self, "external_ref", None)
        return isinstance(raw_value, str) and bool(raw_value)

    @computed_field
    @property
    def external_ref_kind(self) -> str | None:
        raw_value = getattr(self, "external_ref", None)
        return vault_reference_kind(raw_value) if isinstance(raw_value, str) else None

    @computed_field
    @property
    def secret_metadata(self) -> dict[str, object]:
        if self.secret_fingerprint:
            return hosted_secret_metadata(
                provider="hosted",
                encryption_key_id=self.encryption_key_id,
                secret_fingerprint=self.secret_fingerprint,
            ).to_api_dict()
        raw_value = getattr(self, "external_ref", None)
        external_ref = raw_value if isinstance(raw_value, str) else ""
        return external_vault_reference_metadata(
            provider=self.provider,
            external_ref=external_ref,
        ).to_api_dict()
