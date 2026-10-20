"""Public flow authoring and immutable revision contracts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from opsmesh.orchestration.definitions.contracts import FlowDefinitionSpec, FlowValue
from opsmesh.orchestration.execution.contracts import ExecutionLimits


class AgentTemplate(FlowValue):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=100000)
    model_slot: str = Field(default="default", min_length=1, max_length=64)
    tools: list[str] = Field(default_factory=list, max_length=100)
    mcp: list[str] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=100)
    handoffs: list[str] = Field(default_factory=list, max_length=100)
    agent_tools: list[str] = Field(default_factory=list, max_length=100)
    output_schema: dict[str, Any] | None = None


class AgentGraphSpec(FlowValue):
    entry_agent: str = Field(pattern="^[a-zA-Z0-9_-]{1,64}$")
    agents: dict[str, AgentTemplate] = Field(min_length=1, max_length=100)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def valid_graph(self) -> "AgentGraphSpec":
        if self.entry_agent not in self.agents:
            raise ValueError("Entry agent is not defined")
        for key, agent in self.agents.items():
            if any(target not in self.agents for target in agent.handoffs + agent.agent_tools):
                raise ValueError("Agent graph references an undefined agent")
            for target in agent.agent_tools:
                stack = [(target, 1)]
                while stack:
                    current, depth = stack.pop()
                    if current == key or depth > 8:
                        raise ValueError("Agent tools contain recursion or exceed depth 8")
                    stack.extend((name, depth + 1) for name in self.agents[current].agent_tools)
        return self


class DefinitionCreate(FlowValue):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["agent_graph", "flow"] = "flow"
    visibility: Literal["private", "public"] = "private"
    spec: AgentGraphSpec | FlowDefinitionSpec

    @model_validator(mode="after")
    def kind_valid(self) -> "DefinitionCreate":
        if (self.kind == "agent_graph") != isinstance(self.spec, AgentGraphSpec):
            raise ValueError("Definition kind does not match its specification")
        return self


class DefinitionUpdate(FlowValue):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    visibility: Literal["private", "public"] = "private"
    spec: AgentGraphSpec | FlowDefinitionSpec


class DefinitionView(FlowValue):
    id: UUID
    name: str
    owner_id: UUID
    kind: str
    visibility: str
    status: str
    revision: int
    # Mutable authoring state is private to the owner/editors. Public readers
    # use DefinitionRevisionView for the immutable published contract.
    draft: AgentGraphSpec | FlowDefinitionSpec | None = None


class FlowPublishRequest(FlowValue):
    expected_revision: int = Field(ge=1)


class DefinitionRevisionView(FlowValue):
    id: UUID
    definition_id: UUID
    number: int
    digest: str
    spec: AgentGraphSpec | FlowDefinitionSpec


class FlowAuthoringContract(FlowValue):
    node_kinds: list[str]
    mapping_roots: list[str]
    max_nodes: int
    max_nested_depth: int
    definition_schema: dict[str, Any]


class FlowValidationResult(FlowValue):
    valid: bool
    revision: int


Permission = Literal["read", "use", "edit", "manage"]


class DefinitionGrantRequest(FlowValue):
    user_id: UUID
    permissions: list[Permission] = Field(min_length=1, max_length=4)
