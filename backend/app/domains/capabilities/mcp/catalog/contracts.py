from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer, model_validator

from backend.app.core.contracts import TimestampedModel
from backend.app.core.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.core.security.secrets import (
    external_vault_reference_metadata,
    hosted_secret_metadata,
    vault_reference_kind,
)
from backend.app.domains.capabilities.mcp.catalog.redaction import redacted_connection


class McpServerCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    server_type: Literal["stdio", "streamable_http", "sse", "hosted"] = "stdio"
    connection: dict[str, object] = Field(default_factory=dict)
    visibility: str = Field(default="private", pattern="^(private|public)$")


class McpServerUpdateRequest(BaseModel):
    connection: dict[str, object] | None = None
    visibility: str | None = Field(default=None, pattern="^(private|public)$")


class McpServerHealthCheckRequest(BaseModel):
    health_status: str = Field(pattern="^(healthy|unhealthy|unknown)$")
    error_code: str | None = Field(default=None, max_length=120)


class McpServerResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    server_type: str
    connection: dict[str, object]
    visibility: str
    status: str
    health_status: str
    last_health_check_at: datetime | None
    last_error: str | None

    @field_serializer("connection")
    def _serialize_connection(self, connection: dict[str, object]) -> dict[str, object]:
        return redacted_connection(connection)

    @field_serializer("last_error")
    def _serialize_last_error(self, value: str | None) -> str | None:
        return redact_sensitive_text(value) if value is not None else None


class McpToolAllowRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    input_schema: dict[str, object] = Field(default_factory=dict)
    capability_key: str | None = Field(default=None, max_length=120)
    requires_approval: bool = False
    risk_level: str = Field(default="low", max_length=32)
    policy: dict[str, object] = Field(default_factory=dict)


class McpToolAllowUpdateRequest(BaseModel):
    tool_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    input_schema: dict[str, object] | None = None
    capability_key: str | None = Field(default=None, max_length=120)
    requires_approval: bool | None = None
    risk_level: str | None = Field(default=None, max_length=32)
    policy: dict[str, object] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "McpToolAllowUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one MCP tool field is required")
        null_fields = sorted(
            field
            for field in self.model_fields_set
            if getattr(self, field) is None and field != "capability_key"
        )
        if null_fields:
            raise ValueError(f"MCP tool fields cannot be null: {', '.join(null_fields)}")
        return self


class McpToolAllowResponse(TimestampedModel):
    workspace_id: UUID
    mcp_server_id: UUID
    tool_name: str
    description: str
    input_schema: dict[str, object]
    capability_key: str | None
    requires_approval: bool
    risk_level: str
    policy: dict[str, object]
    status: str

    @field_serializer("policy")
    def _serialize_policy(self, value: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(value)


class McpCredentialReferenceCreateRequest(BaseModel):
    mcp_server_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=80)
    external_ref: str = Field(default="", max_length=512)
    secret_payload: dict[str, object] | None = None
    scopes: list[str] = Field(default_factory=list)


class McpCredentialReferenceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    scopes: list[str] | None = None


class McpCredentialReferenceRotateRequest(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    external_ref: str | None = Field(default=None, max_length=512)
    secret_payload: dict[str, object] | None = None

    @model_validator(mode="after")
    def validate_rotation_source(self) -> "McpCredentialReferenceRotateRequest":
        has_external_ref = bool(self.external_ref and self.external_ref.strip())
        has_secret_payload = self.secret_payload is not None
        if has_external_ref == has_secret_payload:
            raise ValueError("Provide exactly one of external_ref or secret_payload")
        if has_external_ref and not self.provider:
            raise ValueError("provider is required when rotating to an external reference")
        if has_external_ref and self.provider == "hosted":
            raise ValueError("provider hosted requires secret_payload")
        return self


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def external_ref_configured(self) -> bool:
        raw_value = getattr(self, "external_ref", None)
        return isinstance(raw_value, str) and bool(raw_value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def external_ref_kind(self) -> str | None:
        raw_value = getattr(self, "external_ref", None)
        return vault_reference_kind(raw_value) if isinstance(raw_value, str) else None

    @computed_field  # type: ignore[prop-decorator]
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
