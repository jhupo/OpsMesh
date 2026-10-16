"""Package manifest: exports, permissions, effects, configuration and data policy.

The manifest is the declaration a package installs with. Publishing does not
execute code; the server validates the declaration before an installation can
bind or invoke any export.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal

from jsonschema import Draft202012Validator
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

MAX_EXPORTS_PER_PACKAGE: Final = 256
MAX_SCHEMA_BYTES: Final = 65_536
MAX_PERMISSIONS: Final = 128


class ExportKind(StrEnum):
    OPERATION = "operation"
    HOOK = "hook"
    EVENT = "event"


class HookStage(StrEnum):
    """The fixed hook chain stages from V4 section 6.1."""

    CONTEXT_PREPARE = "context.prepare"
    MODEL_BEFORE = "model.before"
    MODEL_AFTER = "model.after"
    TOOL_BEFORE = "tool.before"
    TOOL_AFTER = "tool.after"
    INPUT_GUARDRAIL = "input_guardrail"
    OUTPUT_GUARDRAIL = "output_guardrail"
    ON_ERROR = "on_error"
    FINALIZE = "finalize"


class EffectKind(StrEnum):
    """External effect classes a declaration must name explicitly."""

    NONE = "none"
    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    MODEL_CALL = "model_call"
    FILE_WRITE = "file_write"
    PAYMENT = "payment"


class HookFailurePolicy(StrEnum):
    """Required hooks fail closed; only observers may continue."""

    BLOCK = "block"
    OBSERVE = "observe"


class Permission(BaseModel):
    """A single granted capability string, e.g. ``governance.audit.append``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=1, max_length=500)

    @property
    def namespace(self) -> str:
        return self.name.split(".", 1)[0]


class JsonSchema(BaseModel):
    """A declared JSON Schema with the package's size bound enforced."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    type: Literal["object"] = "object"
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    required: tuple[str, ...] = ()
    # JSON Schema spells this ``additionalProperties``; accepting both spellings
    # keeps the declared schema and its exported document round-trip stable.
    additional_properties: bool = Field(
        default=False,
        validation_alias=AliasChoices("additional_properties", "additionalProperties"),
    )

    def model_dump_schema(self) -> dict[str, JsonValue]:
        schema: dict[str, JsonValue] = {
            "type": self.type,
            "properties": dict(self.properties),
            "additionalProperties": self.additional_properties,
        }
        if self.required:
            schema["required"] = list(self.required)
        return schema

    def validate_instance(self, instance: JsonValue) -> None:
        """Validate one instance; an empty declaration constrains nothing."""
        if not self.properties and not self.required:
            return
        Draft202012Validator(self.model_dump_schema()).validate(instance)


class OperationExport(BaseModel):
    """A declared operation: typed I/O, effects, timeout and permissions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    export_id: str = Field(min_length=1, max_length=100)
    kind: Literal[ExportKind.OPERATION] = ExportKind.OPERATION
    description: str = Field(min_length=1, max_length=2000)
    input_schema: JsonSchema = Field(default_factory=JsonSchema)
    output_schema: JsonSchema = Field(default_factory=JsonSchema)
    effects: tuple[EffectKind, ...] = ()
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    permissions: tuple[str, ...] = ()

    @property
    def has_external_effect(self) -> bool:
        return any(effect is not EffectKind.NONE for effect in self.effects)


class HookExport(BaseModel):
    """A declared hook bound to fixed stages with an explicit failure policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    export_id: str = Field(min_length=1, max_length=100)
    kind: Literal[ExportKind.HOOK] = ExportKind.HOOK
    description: str = Field(min_length=1, max_length=2000)
    stages: tuple[HookStage, ...] = Field(min_length=1)
    input_schema: JsonSchema = Field(default_factory=JsonSchema)
    patch_fields: tuple[str, ...] = ()
    failure_policy: HookFailurePolicy = HookFailurePolicy.BLOCK


class EventExport(BaseModel):
    """A declared event consumer: sources, flow, filter scope and idempotency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    export_id: str = Field(min_length=1, max_length=100)
    kind: Literal[ExportKind.EVENT] = ExportKind.EVENT
    description: str = Field(min_length=1, max_length=2000)
    source_types: tuple[str, ...] = Field(min_length=1)
    source_versions: tuple[str, ...] = Field(min_length=1)
    operation_export_id: str = Field(min_length=1, max_length=100)
    filter_scope: tuple[str, ...] = ()
    idempotency_key_fields: tuple[str, ...] = Field(min_length=1)


PackageExport = OperationExport | HookExport | EventExport


class DataPolicy(BaseModel):
    """What business data a package stores and where."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stores_business_data: bool = False
    namespaces: tuple[str, ...] = ()
    retention_days: int | None = Field(default=None, ge=1)
    contains_user_content: bool = False


class PackageManifest(BaseModel):
    """The complete installation declaration of one package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=2000)
    operations: tuple[OperationExport, ...] = ()
    hooks: tuple[HookExport, ...] = ()
    events: tuple[EventExport, ...] = ()
    permissions: tuple[Permission, ...] = Field(default=(), max_length=MAX_PERMISSIONS)
    configuration_schema: JsonSchema = Field(default_factory=JsonSchema)
    data_policy: DataPolicy = Field(default_factory=DataPolicy)

    @model_validator(mode="after")
    def validate_declarations(self) -> PackageManifest:
        errors: list[str] = []
        exports: list[PackageExport] = [*self.operations, *self.hooks, *self.events]
        if len(exports) > MAX_EXPORTS_PER_PACKAGE:
            errors.append(f"a package declares at most {MAX_EXPORTS_PER_PACKAGE} exports")
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
        declared = {permission.name for permission in self.permissions}
        for operation in self.operations:
            for permission in operation.permissions:
                if permission not in declared:
                    errors.append(
                        f"operation {operation.export_id!r} uses undeclared permission "
                        f"{permission!r}"
                    )
        for export in exports:
            schema = (
                export.input_schema if isinstance(export, OperationExport | HookExport) else None
            )
            if schema is not None and len(repr(schema.model_dump_schema())) > MAX_SCHEMA_BYTES:
                errors.append(f"export {export.export_id!r} input schema exceeds the size bound")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @property
    def export_ids(self) -> tuple[str, ...]:
        exports: list[PackageExport] = [*self.operations, *self.hooks, *self.events]
        return tuple(export.export_id for export in exports)


def validate_manifest(payload: dict[str, JsonValue]) -> PackageManifest:
    """Validate a raw manifest payload, raising ``ValidationError`` on failure."""
    from opsmesh_sdk.errors import ValidationError

    try:
        return PackageManifest.model_validate(payload)
    except ValueError as exc:
        raise ValidationError(
            "The package manifest violates its declaration contract",
            details={"reason": str(exc)},
        ) from exc
