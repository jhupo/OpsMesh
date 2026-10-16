"""Extension definition contracts: declared operations, hooks and events.

An extension capability declares the exports its package installs with —
typed operations with their effects, hooks bound to fixed stages, and
event consumers — plus the permissions those exports need. The
declaration is frozen at publish; nothing is imported, registered or run
here. Installation, invocation and the host belong to later owners.
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from opsmesh.capabilities.validation import validate_parameter_schema
from opsmesh.shared.errors import InvalidArgumentError

MAX_EXPORTS = 256
MAX_PERMISSIONS = 128
MAX_PATCH_FIELDS = 32
MAX_DESCRIPTION_LENGTH = 2000
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
    """The export contract one extension definition publishes."""

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
                    f"operation {operation.export_id!r} uses undeclared "
                    f"permissions: {undeclared}"
                )
        if len(set(self.permissions)) != len(self.permissions):
            errors.append("extension permissions must be unique")
        if errors:
            raise ValueError("; ".join(errors))
        return self


def validated_configuration(configuration: object) -> dict[str, object]:
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
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"
    configuration: ExtensionConfiguration


class ExtensionDraftRequest(BaseModel):
    """Replace the current draft configuration of one extension definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: ExtensionConfiguration


__all__ = [
    "EXTENSION_EFFECTS",
    "ExtensionConfiguration",
    "ExtensionCreateRequest",
    "ExtensionDraftRequest",
    "ExtensionEvent",
    "ExtensionHook",
    "ExtensionOperation",
    "HOOK_FAILURE_POLICIES",
    "HOOK_STAGES",
    "validated_configuration",
]
