"""Build native Agents, handoffs and Agent-as-tool relationships from frozen values."""

import asyncio
import json
from typing import Any

from agents import Agent, AgentOutputSchemaBase, ModelSettings
from jsonschema import Draft202012Validator
from openai.types.shared import Reasoning

from opsmesh.orchestration.execution.contracts import PreparedRun
from opsmesh.orchestration.execution.guardrails import native_guardrails
from opsmesh.orchestration.execution.mcp import native_mcp_servers
from opsmesh.orchestration.execution.tools import sandbox_tool


class StructuredOutput(AgentOutputSchemaBase):
    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return "ExecutionOutput"

    def json_schema(self) -> dict[str, Any]:
        return self.schema

    def is_strict_json_schema(self) -> bool:
        return False

    def validate_json(self, json_str: str) -> Any:
        value = json.loads(json_str)
        Draft202012Validator(self.schema).validate(value)
        return value


def build_agents(prepared: PreparedRun, pending_tools: set[asyncio.Task[Any]]) -> Agent[Any]:
    agents: dict[str, Agent[Any]] = {}
    inputs, outputs = native_guardrails(prepared)
    for key, definition in prepared.request.agents.items():
        agents[key] = Agent(
            name=definition.name,
            input_guardrails=inputs,
            output_guardrails=outputs,
            instructions=definition.instructions,
            model=f"{prepared.reference.run_id}:{key}",
            model_settings=ModelSettings(
                max_tokens=definition.model.max_output_tokens,
                temperature=definition.model.temperature,
                reasoning=Reasoning(effort=definition.model.reasoning_effort)
                if definition.model.reasoning_effort is not None
                else None,
            ),
            tools=[sandbox_tool(prepared, key, tool, pending_tools) for tool in definition.tools],
            mcp_servers=native_mcp_servers(prepared, key, definition.mcp_connections),
            output_type=StructuredOutput(definition.output_schema)
            if definition.output_schema is not None
            else None,
        )
    for key, definition in prepared.request.agents.items():
        agents[key].handoffs = [agents[target] for target in definition.handoffs]
        agents[key].tools.extend(
            agents[target].as_tool(
                tool_name=target,
                tool_description=agents[target].name,
                max_turns=prepared.request.limits.max_turns,
            )
            for target in definition.agent_tools
        )
    return agents[prepared.request.entry_agent]
