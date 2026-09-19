from datetime import datetime
from typing import Literal
from uuid import UUID

from opsmesh_plugin_sdk.distribution import PluginReleaseDescriptor, SignedPluginRelease
from opsmesh_plugin_sdk.packages import SignedPluginPackage
from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.contracts import TimestampedModel


class TrustKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    plugin_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    public_key: str = Field(min_length=44, max_length=44)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions: list[str] = Field(min_length=1, max_length=16)
    lifetime_hours: int = Field(default=24, ge=1, le=2160)


class CredentialResponse(BaseModel):
    id: UUID
    token: str
    expires_at: datetime
    permissions: list[str]


class TrustKeyResponse(TimestampedModel):
    workspace_id: UUID
    key_id: str
    plugin_key: str
    public_key: str
    status: str


class CapabilityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: UUID


class PluginInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: SignedPluginPackage
    bindings: dict[str, CapabilityBinding] = Field(min_length=1, max_length=128)
    approved_permissions: list[str] = Field(default_factory=list, max_length=128)
    expected_generation: int | None = Field(default=None, ge=1)


class PluginAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["enable", "disable", "switch_version", "retire_version", "uninstall"]
    expected_generation: int = Field(ge=1)
    version: str | None = Field(default=None, max_length=64)


class PluginInstallResponse(TimestampedModel):
    workspace_id: UUID
    plugin_key: str
    status: str
    generation: int
    current_version: str


class PluginReleaseResponse(TimestampedModel):
    workspace_id: UUID
    install_id: UUID
    version: str
    status: str
    checksum: str
    package: dict[str, object]
    approved_permissions: list[str]


class PluginBindingResponse(TimestampedModel):
    capability_key: str
    kind: str
    resource_id: UUID
    release_id: UUID
    configuration: dict[str, object]


class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(max_length=2048)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    allowed_hosts: list[str] = Field(min_length=1, max_length=8)
    enabled: bool = True


class SourceUpdate(SourceSettings):
    expected_generation: int = Field(ge=1)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")


class CandidateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bindings: dict[str, CapabilityBinding] = Field(min_length=1, max_length=128)
    approved_permissions: list[str] = Field(default_factory=list, max_length=128)
    expected_generation: int | None = Field(default=None, ge=1)
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CandidatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bindings: dict[str, CapabilityBinding] = Field(default_factory=dict, max_length=128)


class SourceResponse(TimestampedModel):
    workspace_id: UUID
    name: str
    url: str
    sha256: str
    allowed_hosts: list[str]
    enabled: bool
    generation: int
    synced_generation: int


class CandidateResponse(TimestampedModel):
    source_id: UUID
    plugin_key: str
    version: str
    publisher_key_id: str
    sha256: str
    withdrawn: bool
    verified_release: SignedPluginRelease | None


class BindingPreview(BaseModel):
    resource_id: UUID
    configuration: dict[str, object]


class CandidatePreviewResponse(BaseModel):
    candidate_id: UUID
    source_generation: int
    sha256: str
    release: PluginReleaseDescriptor
    expected_generation: int | None
    current_version: str | None
    required_permissions: list[str]
    added_permissions: list[str]
    removed_permissions: list[str]
    configuration_schemas: dict[str, dict[str, object]]
    current_bindings: dict[str, BindingPreview]
    proposed_bindings: dict[str, BindingPreview]
    preview_digest: str


class DownloadResponse(TimestampedModel):
    source_id: UUID
    candidate_id: UUID | None
    status: str
    attempts: int
    error_code: str | None
