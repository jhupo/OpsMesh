from datetime import datetime
from typing import Literal
from uuid import UUID

from opsmesh_plugin_sdk.packaging.distribution import PluginReleaseDescriptor, SignedPluginRelease
from opsmesh_plugin_sdk.packaging.packages import SignedPluginPackage
from pydantic import BaseModel, ConfigDict, Field

from opsmesh.shared.contracts import TimestampedContract


class PluginTrustKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    plugin_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    public_key: str = Field(min_length=44, max_length=44)


class PluginCredentialRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions: list[str] = Field(min_length=1, max_length=16)
    lifetime_hours: int = Field(default=24, ge=1, le=2160)


class PluginCredentialResponse(BaseModel):
    id: UUID
    token: str
    expires_at: datetime
    permissions: list[str]


class PluginTrustKeyResponse(TimestampedContract):
    workspace_id: UUID
    key_id: str
    plugin_key: str
    public_key: str
    status: str


class PluginCapabilityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    resource_id: UUID


class PluginInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: SignedPluginPackage
    instance_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    installation_id: UUID | None = None
    bindings: list[PluginCapabilityBinding] = Field(default_factory=list, max_length=256)
    approved_permissions: list[str] = Field(default_factory=list, max_length=128)
    expected_generation: int | None = Field(default=None, ge=1)


class PluginActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["enable", "disable", "switch_version", "retire_version", "uninstall"]
    expected_generation: int = Field(ge=1)
    version: str | None = Field(default=None, max_length=64)
    approved_permissions: list[str] | None = Field(default=None, max_length=128)


class PluginBindingRequest(PluginCapabilityBinding):
    expected_generation: int = Field(ge=1)


class PluginBindingReauthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_generation: int = Field(ge=1)
    expected_configuration_revision: int = Field(ge=1)


class PluginInstallResponse(TimestampedContract):
    workspace_id: UUID
    plugin_key: str
    instance_name: str
    status: str
    platform_blocked: bool
    generation: int
    current_version: str
    current_release_id: UUID
    approved_permissions: list[str]


class PluginReleaseResponse(TimestampedContract):
    workspace_id: UUID
    plugin_key: str
    version: str
    status: str
    checksum: str
    package: dict[str, object]


class PluginBindingResponse(TimestampedContract):
    capability_key: str
    kind: str
    resource_id: UUID
    configuration_revision: int
    configuration: dict[str, object]


class PluginSourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(max_length=2048)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    allowed_hosts: list[str] = Field(min_length=1, max_length=8)
    enabled: bool = True


class PluginSourceUpdateRequest(PluginSourceSettings):
    expected_generation: int = Field(ge=1)


class PluginDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")


class PluginCandidateInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    installation_id: UUID | None = None
    bindings: list[PluginCapabilityBinding] = Field(default_factory=list, max_length=256)
    approved_permissions: list[str] = Field(default_factory=list, max_length=128)
    expected_generation: int | None = Field(default=None, ge=1)
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PluginCandidatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: UUID | None = None
    bindings: list[PluginCapabilityBinding] = Field(default_factory=list, max_length=256)


class PluginSourceResponse(TimestampedContract):
    workspace_id: UUID
    name: str
    url: str
    sha256: str
    allowed_hosts: list[str]
    enabled: bool
    generation: int
    synced_generation: int


class PluginCandidateResponse(TimestampedContract):
    source_id: UUID
    plugin_key: str
    version: str
    publisher_key_id: str
    sha256: str
    withdrawn: bool
    verified_release: SignedPluginRelease | None


class PluginBindingPreview(BaseModel):
    capability_key: str
    resource_id: UUID
    configuration: dict[str, object]


class PluginCandidatePreviewResponse(BaseModel):
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
    current_bindings: list[PluginBindingPreview]
    proposed_bindings: list[PluginBindingPreview]
    preview_digest: str


class PluginDownloadResponse(TimestampedContract):
    source_id: UUID
    candidate_id: UUID | None
    execution_id: UUID
    status: str
    attempts: int
    error_code: str | None
