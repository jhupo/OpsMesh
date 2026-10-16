"""Capability catalog, version and package HTTP contracts.

The contracts shared by all seven definition types live here, and so do
the channel and extension type configurations: per the V4 target tree
those two types keep their declarations in the root schemas and
validation modules instead of owning a sub-package, while the other five
types validate their configuration in their sub-package.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from opsmesh.capabilities.models import (
    MAX_DESCRIPTION_LENGTH,
    CapabilityVersion,
)
from opsmesh.capabilities.validation import (
    validate_parameter_schema,
    validate_secret_slots,
)
from opsmesh.shared.errors import InvalidArgumentError

CapabilityTypeName = Literal["expert", "team", "mcp", "skill", "tool", "channel", "extension"]
VisibilityName = Literal["private", "public"]
DefinitionStatusName = Literal["active", "disabled", "deleted"]
VersionStatusName = Literal["draft", "published"]

MAX_OPERATIONS = 4
MAX_ENTRYPOINT_LENGTH = 300
MAX_PROTOCOL_LENGTH = 50
SIGNATURE_ALGORITHMS = ("hmac-sha256",)
CHANNEL_OPERATIONS = ("receive", "send", "connect", "disconnect")
REQUIRED_CHANNEL_OPERATIONS = ("receive", "send")
CHANNEL_ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
PROTOCOL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,48}[a-z0-9]$")

MAX_EXPORTS = 256
MAX_PERMISSIONS = 128
MAX_PATCH_FIELDS = 32
MAX_EXPORT_ID_LENGTH = 100
EXTENSION_EFFECTS = (
    "none",
    "read",
    "write",
    "network",
    "model_call",
    "file_write",
    "payment",
)
HOOK_STAGES = (
    "context.prepare",
    "model.before",
    "model.after",
    "tool.before",
    "tool.after",
    "input_guardrail",
    "output_guardrail",
    "on_error",
    "finalize",
)
HOOK_FAILURE_POLICIES = ("block", "observe")


class CapabilityDefinitionView(BaseModel):
    """One definition as the catalog exposes it; content lives in versions."""

    model_config = ConfigDict(extra="forbid")

    definition_id: UUID
    type: str
    key: str
    name: str
    description: str
    visibility: str
    status: str
    source: str
    created_by_user_id: UUID | None
    latest_version: int
    has_published_version: bool
    created_at: datetime
    updated_at: datetime


class CapabilityDefinitionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CapabilityDefinitionView]
    total: int


class CapabilityVersionView(BaseModel):
    """One immutable-or-draft version with its full declared content."""

    model_config = ConfigDict(extra="forbid")

    definition_id: UUID
    version: int
    status: str
    configuration: dict[str, object]
    resource_manifest: list[dict[str, object]]
    dependencies: list[dict[str, object]]
    flow_templates: list[dict[str, object]]
    digest: str | None
    package_digest: str | None
    package_size: int | None
    package_filename: str | None
    published_at: datetime | None
    published_by_user_id: UUID | None
    created_at: datetime


class UpdateDefinitionRequest(BaseModel):
    """The definition-level metadata an author may change."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class UpdateDefinitionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DefinitionStatusName


class UpdateDefinitionVisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: VisibilityName


def version_view(version: CapabilityVersion, package_filename: str | None) -> CapabilityVersionView:
    """Map one stored version onto its HTTP contract."""
    return CapabilityVersionView(
        definition_id=version.definition_id,
        version=version.version,
        status=version.status,
        configuration=version.configuration,
        resource_manifest=list(version.resource_manifest),
        dependencies=list(version.dependencies),
        flow_templates=list(version.flow_templates),
        digest=version.digest,
        package_digest=version.package_digest,
        package_size=version.package_size,
        package_filename=package_filename,
        published_at=version.published_at,
        published_by_user_id=version.published_by_user_id,
        created_at=version.created_at,
    )


class ChannelOperation(BaseModel):
    """One adapter operation with its identifier and typed I/O schemas."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["receive", "send", "connect", "disconnect"]
    entrypoint: str = Field(min_length=1, max_length=MAX_ENTRYPOINT_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schemas(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)


class ChannelConfiguration(BaseModel):
    """The vendor protocol one channel definition speaks.

    A channel declares one vendor protocol and the adapter operations
    that speak it. The declaration is static: an entrypoint is an
    identifier the package must carry as a declared resource, and no
    vendor client is constructed, no signature is verified and no
    message is exchanged at publish time.
    """

    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(min_length=2, max_length=MAX_PROTOCOL_LENGTH)
    signature_algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    operations: list[ChannelOperation] = Field(min_length=1, max_length=MAX_OPERATIONS)
    parameters_schema: dict[str, object] = Field(default_factory=dict)
    secret_slots: list[str] = Field(default_factory=list)

    @field_validator("parameters_schema")
    @classmethod
    def _parameters_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

    @field_validator("secret_slots")
    @classmethod
    def _secret_slots(cls, value: list[str]) -> list[str]:
        return validate_secret_slots(value)

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, value: str) -> str:
        if not PROTOCOL_PATTERN.match(value):
            raise ValueError("a channel protocol is a lowercase vendor identifier")
        return value

    @field_validator("operations")
    @classmethod
    def _operations(cls, value: list[ChannelOperation]) -> list[ChannelOperation]:
        names = [operation.name for operation in value]
        if len(set(names)) != len(names):
            raise ValueError("channel operation names must be unique")
        missing = sorted(set(REQUIRED_CHANNEL_OPERATIONS) - set(names))
        if missing:
            raise ValueError(f"a channel must declare the operations: {missing}")
        for operation in value:
            if not CHANNEL_ENTRYPOINT_PATTERN.match(operation.entrypoint):
                raise ValueError("a channel entrypoint must name module:function")
        return value


def validated_channel_configuration(configuration: object) -> dict[str, object]:
    """Validate one channel configuration, mapping failures to the domain error."""
    try:
        model = ChannelConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The channel configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class ChannelCreateRequest(BaseModel):
    """Create one channel definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    visibility: VisibilityName = "private"
    configuration: ChannelConfiguration


class ChannelDraftRequest(BaseModel):
    """Replace the current draft configuration of one channel definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: ChannelConfiguration


class ExtensionOperation(BaseModel):
    """One declared operation: typed I/O, effects, timeout and permissions."""

    model_config = ConfigDict(extra="forbid")

    export_id: str = Field(min_length=1, max_length=MAX_EXPORT_ID_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    effects: list[str] = Field(default_factory=list, max_length=len(EXTENSION_EFFECTS))
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    permissions: list[str] = Field(default_factory=list, max_length=MAX_PERMISSIONS)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schemas(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

    @field_validator("effects")
    @classmethod
    def _known_effects(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(EXTENSION_EFFECTS))
        if unknown:
            raise ValueError(f"unknown extension effects: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("extension effects must be unique")
        return value

    @field_validator("permissions")
    @classmethod
    def _permissions(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("operation permissions must be unique")
        return value


class ExtensionHook(BaseModel):
    """One declared hook bound to fixed stages with a failure policy."""

    model_config = ConfigDict(extra="forbid")

    export_id: str = Field(min_length=1, max_length=MAX_EXPORT_ID_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    stages: list[str] = Field(min_length=1, max_length=len(HOOK_STAGES))
    input_schema: dict[str, object] = Field(default_factory=dict)
    patch_fields: list[str] = Field(default_factory=list, max_length=MAX_PATCH_FIELDS)
    failure_policy: Literal["block", "observe"] = "block"

    @field_validator("input_schema")
    @classmethod
    def _schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

    @field_validator("stages")
    @classmethod
    def _known_stages(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - set(HOOK_STAGES))
        if unknown:
            raise ValueError(f"unknown hook stages: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("hook stages must be unique")
        return value

    @field_validator("patch_fields")
    @classmethod
    def _patch_fields(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("hook patch fields must be unique")
        return value


class ExtensionEvent(BaseModel):
    """One declared event consumer with its operation and idempotency fields."""

    model_config = ConfigDict(extra="forbid")

    export_id: str = Field(min_length=1, max_length=MAX_EXPORT_ID_LENGTH)
    description: str = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)
    source_types: list[str] = Field(min_length=1)
    source_versions: list[str] = Field(min_length=1)
    operation_export_id: str = Field(min_length=1, max_length=MAX_EXPORT_ID_LENGTH)
    filter_scope: list[str] = Field(default_factory=list)
    idempotency_key_fields: list[str] = Field(min_length=1)


class ExtensionConfiguration(BaseModel):
    """The export contract one extension definition publishes.

    An extension declares the exports its package installs with — typed
    operations with their effects, hooks bound to fixed stages, and event
    consumers — plus the permissions those exports need. The declaration
    is frozen at publish; nothing is imported, registered or run here.
    """

    model_config = ConfigDict(extra="forbid")

    operations: list[ExtensionOperation] = Field(default_factory=list, max_length=MAX_EXPORTS)
    hooks: list[ExtensionHook] = Field(default_factory=list, max_length=MAX_EXPORTS)
    events: list[ExtensionEvent] = Field(default_factory=list, max_length=MAX_EXPORTS)
    permissions: list[str] = Field(default_factory=list, max_length=MAX_PERMISSIONS)

    @model_validator(mode="after")
    def _coherent_declarations(self) -> ExtensionConfiguration:
        errors: list[str] = []
        exports: list[ExtensionOperation | ExtensionHook | ExtensionEvent] = [
            *self.operations,
            *self.hooks,
            *self.events,
        ]
        if len(exports) > MAX_EXPORTS:
            errors.append(f"an extension declares at most {MAX_EXPORTS} exports")
        seen: set[str] = set()
        for export in exports:
            if export.export_id in seen:
                errors.append(f"duplicate export_id {export.export_id!r}")
            seen.add(export.export_id)
        operation_ids = {operation.export_id for operation in self.operations}
        for event in self.events:
            if event.operation_export_id not in operation_ids:
                errors.append(
                    f"event {event.export_id!r} consumes unknown operation "
                    f"{event.operation_export_id!r}"
                )
        declared = set(self.permissions)
        for operation in self.operations:
            undeclared = sorted(set(operation.permissions) - declared)
            if undeclared:
                errors.append(
                    f"operation {operation.export_id!r} uses undeclared permissions: {undeclared}"
                )
        if len(set(self.permissions)) != len(self.permissions):
            errors.append("extension permissions must be unique")
        if errors:
            raise ValueError("; ".join(errors))
        return self


def validated_extension_configuration(configuration: object) -> dict[str, object]:
    """Validate one extension configuration, mapping failures to the domain error."""
    try:
        model = ExtensionConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The extension configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class ExtensionCreateRequest(BaseModel):
    """Create one extension definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    visibility: VisibilityName = "private"
    configuration: ExtensionConfiguration


class ExtensionDraftRequest(BaseModel):
    """Replace the current draft configuration of one extension definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: ExtensionConfiguration
