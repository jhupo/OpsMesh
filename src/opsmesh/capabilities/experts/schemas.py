"""Expert definition contracts: instructions, model requirements, slots, guardrails.

The configuration is the versioned content of one expert definition; it is
validated by the same pydantic contract on the HTTP path and on the
provisioning path, so an internal caller cannot write a shape the API
would refuse.
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

from opsmesh.capabilities.validation import (
    validate_parameter_schema,
    validate_secret_slots,
)
from opsmesh.shared.errors import InvalidArgumentError

MAX_INSTRUCTION_LENGTH = 100_000
MAX_GUARDRAIL_LENGTH = 100
MAX_SLOT_NAME_LENGTH = 100
MAX_MODEL_NAME_LENGTH = 100
EXPERT_SLOT_KINDS = ("tool", "mcp", "skill")


class ModelRequirements(BaseModel):
    """The model an expert expects; selection itself belongs to execution."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=MAX_MODEL_NAME_LENGTH)
    reasoning: str | None = Field(default=None, max_length=40)


class CapabilitySlot(BaseModel):
    """One named capability slot an expert fills through fixed dependencies."""

    model_config = ConfigDict(extra="forbid")

    slot: str
    kind: Literal["tool", "mcp", "skill"]


class ExpertConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str
    prompt: str = ""
    model_requirements: ModelRequirements | None = None
    guardrails: list[str] = []
    capability_slots: list[CapabilitySlot] = []
    parameters_schema: dict[str, object] = {}
    secret_slots: list[str] = []

    @field_validator("instructions")
    @classmethod
    def _instructions(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("expert instructions must not be empty")
        if len(value) > MAX_INSTRUCTION_LENGTH:
            raise ValueError("expert instructions are too long")
        return value

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        if len(value) > MAX_INSTRUCTION_LENGTH:
            raise ValueError("the expert prompt is too long")
        return value

    @field_validator("guardrails")
    @classmethod
    def _guardrails(cls, value: list[str]) -> list[str]:
        for name in value:
            if not name.strip():
                raise ValueError("a guardrail name must not be empty")
            if len(name) > MAX_GUARDRAIL_LENGTH:
                raise ValueError("a guardrail name is too long")
        if len(set(value)) != len(value):
            raise ValueError("guardrail names must be unique")
        return value

    @field_validator("parameters_schema")
    @classmethod
    def _parameters_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)

    @field_validator("secret_slots")
    @classmethod
    def _secret_slots(cls, value: list[str]) -> list[str]:
        return validate_secret_slots(value)

    @model_validator(mode="after")
    def _unique_slots(self) -> ExpertConfiguration:
        names = [slot.slot for slot in self.capability_slots]
        if len(set(names)) != len(names):
            raise ValueError("capability slot names must be unique")
        for name in names:
            if len(name) > MAX_SLOT_NAME_LENGTH:
                raise ValueError("a capability slot name is too long")
        return self


def validated_configuration(configuration: object) -> dict[str, object]:
    """Validate one expert configuration, mapping failures to the domain error."""
    try:
        model = ExpertConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The expert configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class ExpertCreateRequest(BaseModel):
    """Create one expert definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"
    configuration: ExpertConfiguration


class ExpertDraftRequest(BaseModel):
    """Replace the current draft configuration of one expert definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: ExpertConfiguration
