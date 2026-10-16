"""Team definition contracts: members, roles, entrypoint, handoffs and tools.

A team definition is a static collaboration graph over expert versions.
The graph is validated here (unique roles, a reachable entrypoint, an
acyclic handoff graph); running a team belongs to execution and its
collaboration business to the team-collaboration extension.
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
    detect_dependency_cycle,
    validate_parameter_schema,
    validate_secret_slots,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError

MAX_MEMBERS = 32
MAX_ROLE_LENGTH = 100
MAX_SLOT_LENGTH = 100
MAX_EXPERT_KEY_LENGTH = 100
MAX_TOOL_NAME_LENGTH = 100


class TeamMember(BaseModel):
    """One expert member filling one named slot with one unique role."""

    model_config = ConfigDict(extra="forbid")

    slot: str = Field(min_length=1, max_length=MAX_SLOT_LENGTH)
    expert_key: str = Field(min_length=1, max_length=MAX_EXPERT_KEY_LENGTH)
    role: str = Field(min_length=1, max_length=MAX_ROLE_LENGTH)


class Handoff(BaseModel):
    """One directed handoff edge between two member slots."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=MAX_SLOT_LENGTH)
    target: str = Field(min_length=1, max_length=MAX_SLOT_LENGTH)


class AgentAsTool(BaseModel):
    """One member exposed to another member as a callable tool."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=MAX_SLOT_LENGTH)
    tool: str = Field(min_length=1, max_length=MAX_TOOL_NAME_LENGTH)


class TeamConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    members: list[TeamMember] = Field(min_length=1, max_length=MAX_MEMBERS)
    entrypoint: str = Field(min_length=1, max_length=MAX_SLOT_LENGTH)
    handoffs: list[Handoff] = Field(default_factory=list, max_length=MAX_MEMBERS * 4)
    agents_as_tools: list[AgentAsTool] = Field(default_factory=list, max_length=MAX_MEMBERS * 4)
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
    def _coherent_graph(self) -> TeamConfiguration:
        slots = [member.slot for member in self.members]
        if len(set(slots)) != len(slots):
            raise ValueError("team member slots must be unique")
        roles = [member.role for member in self.members]
        if len(set(roles)) != len(roles):
            raise ValueError("team member roles must be unique")
        if self.entrypoint not in slots:
            raise ValueError("the team entrypoint must name a member slot")
        for edge in self.handoffs:
            if edge.source not in slots or edge.target not in slots:
                raise ValueError("a handoff must connect declared member slots")
        for exposure in self.agents_as_tools:
            if exposure.source not in slots:
                raise ValueError("an agent-as-tool exposure must name a declared member slot")
        try:
            detect_dependency_cycle(
                {
                    member.slot: [
                        edge.target for edge in self.handoffs if edge.source == member.slot
                    ]
                    for member in self.members
                },
                subject="team handoffs",
            )
        except ConflictError as exc:
            # A cyclic graph violates the configuration contract: raise a pydantic
            # value error so validated_configuration maps it uniformly.
            raise ValueError(str(exc)) from exc
        return self


def validated_configuration(configuration: object) -> dict[str, object]:
    """Validate one team configuration, mapping failures to the domain error."""
    try:
        model = TeamConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The team configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


class TeamCreateRequest(BaseModel):
    """Create one team definition with its first draft version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    visibility: Literal["private", "public"] = "private"
    configuration: TeamConfiguration


class TeamDraftRequest(BaseModel):
    """Replace the current draft configuration of one team definition."""

    model_config = ConfigDict(extra="forbid")

    configuration: TeamConfiguration
