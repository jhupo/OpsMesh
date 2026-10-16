"""Tool definition contracts: execution mode, entrypoint and operations.

A tool declares how it is executed and what every operation accepts,
returns, effects and approval it requires. Validation is static: an SDK
export entrypoint is checked as an identifier path and a script
entrypoint as a packaged resource, but no module is ever imported and no
script is ever run at publish time. Direct execution is reserved for
trusted system registration; an uploaded package cannot claim it.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from opsmesh.capabilities.validation import (
    validate_parameter_schema,
    validate_secret_slots,
)
from opsmesh.shared.errors import InvalidArgumentError

MAX_OPERATIONS = 256
MAX_ENTRYPOINT_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 2000
TOOL_EFFECTS = (
    "model",
    "file_read",
    "file_write",
    "shell",
    "network",
    "database",
    "external_api",
)
EXECUTION_MODES = ("sdk_export", "script")
SYSTEM_EXECUTION_MODES = (*EXECUTION_MODES, "direct")
ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


class ToolOperation(BaseModel):
    """One callable operation with its schemas, effects and approval demand."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    effects: list[str] = Field(default_factory=list, max_length=len(TOOL_EFFECTS))
    requires_approval: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schemas(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

    @model_validator(mode="after")
    def _known_effects(self) -> ToolOperation:
        unknown = sorted(set(self.effects) - set(TOOL_EFFECTS))
        if unknown:
            raise ValueError(f"unknown tool effects: {unknown}")
        if len(set(self.effects)) != len(self.effects):
            raise ValueError("tool effects must be unique")
        return self


class ToolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_mode: str
    entrypoint: str = Field(min_length=1, max_length=MAX_ENTRYPOINT_LENGTH)
    operations: list[ToolOperation] = Field(min_length=1, max_length=MAX_OPERATIONS)
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

    @model_validator(mode="after")
    def _execution_contract(self) -> ToolConfiguration:
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError(
                "a tool execution mode is one of the user-declared modes; "
                "direct execution is reserved for trusted system registration"
            )
        names = [operation.name for operation in self.operations]
        if len(set(names)) != len(names):
            raise ValueError("tool operation names must be unique")
        if self.execution_mode == "sdk_export" and not ENTRYPOINT_PATTERN.match(self.entrypoint):
            raise ValueError("an SDK export entrypoint must name module:function")
        return self


def validated_configuration(configuration: object) -> dict[str, object]:
    """Validate one tool configuration, mapping failures to the domain error."""
    try:
        model = ToolConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The tool configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class ToolCreateRequest(BaseModel):
    """Create one tool definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"
    configuration: ToolConfiguration


class ToolDraftRequest(BaseModel):
    """Replace the current draft configuration of one tool definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: ToolConfiguration
