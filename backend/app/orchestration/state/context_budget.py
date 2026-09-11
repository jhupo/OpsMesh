from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum

from backend.app.agent_runtime.contracts import (
    AgentRuntimeAgentDefinition,
    AgentRuntimeAgentTool,
    AgentRuntimeOutputSchema,
    AgentRuntimeToolContinuation,
    AgentRuntimeToolDefinition,
)
from backend.app.agent_runtime.core.errors import AgentRuntimePolicyError
from backend.app.agent_runtime.token_estimation import (
    estimate_token_upper_bound,
    truncate_to_token_bound,
)
from backend.app.agents.memory_policy import ContextBudgetPolicy
from backend.app.model_providers.capabilities import resolve_model_capability

DEFAULT_CONTEXT_WINDOW_TOKENS = 32_768
MINIMUM_DYNAMIC_CONTEXT_TOKENS = 512
SERIALIZATION_OVERHEAD_TOKENS = 256


class ContextPriority(IntEnum):
    LOW = 100
    NORMAL = 200
    HIGH = 300
    CRITICAL = 400


@dataclass(frozen=True)
class ContextFragment:
    key: str
    text: str
    priority: ContextPriority
    required: bool = False
    allow_truncation: bool = True


@dataclass(frozen=True)
class ContextFragmentDecision:
    key: str
    priority: str
    required: bool
    status: str
    estimated_tokens: int
    included_tokens: int

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "priority": self.priority,
            "required": self.required,
            "status": self.status,
            "estimated_tokens": self.estimated_tokens,
            "included_tokens": self.included_tokens,
        }


@dataclass(frozen=True)
class ContextBudgetResult:
    text: str
    context_window_tokens: int
    input_budget_tokens: int
    fixed_tokens: int
    dynamic_budget_tokens: int
    included_tokens: int
    decisions: tuple[ContextFragmentDecision, ...]

    def evidence(self) -> dict[str, object]:
        return {
            "estimator": "utf8_bytes_upper_bound",
            "context_window_tokens": self.context_window_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "fixed_tokens": self.fixed_tokens,
            "dynamic_budget_tokens": self.dynamic_budget_tokens,
            "included_tokens": self.included_tokens,
            "excluded_tokens": sum(
                item.estimated_tokens - item.included_tokens for item in self.decisions
            ),
            "components": [item.as_dict() for item in self.decisions],
        }


class ContextBudgetManager:
    """Build a bounded provider input from product-owned context fragments.

    UTF-8 byte length is intentionally used as a conservative cross-provider upper bound. Provider
    session compaction remains delegated to the provider SDK; this manager only bounds the new
    product context added to the turn.
    """

    def build(
        self,
        *,
        fragments: tuple[ContextFragment, ...],
        provider: str | None,
        model: str | None,
        policy: ContextBudgetPolicy,
        instructions: str,
        tool_definitions: tuple[AgentRuntimeToolDefinition, ...] = (),
        continuations: tuple[AgentRuntimeToolContinuation, ...] = (),
        handoff_agents: tuple[AgentRuntimeAgentDefinition, ...] = (),
        agent_tools: tuple[AgentRuntimeAgentTool, ...] = (),
        output_schema: AgentRuntimeOutputSchema | None = None,
    ) -> ContextBudgetResult:
        context_window = context_window_tokens(provider, model, policy)
        fixed_tokens = fixed_context_tokens(
            instructions=instructions,
            tool_definitions=tool_definitions,
            continuations=continuations,
            handoff_agents=handoff_agents,
            agent_tools=agent_tools,
            output_schema=output_schema,
        )
        input_budget = max(
            MINIMUM_DYNAMIC_CONTEXT_TOKENS,
            context_window - policy.output_reserve_tokens - policy.safety_margin_tokens,
        )
        if policy.max_input_tokens is not None:
            input_budget = min(input_budget, policy.max_input_tokens)
        dynamic_budget = input_budget - fixed_tokens
        if dynamic_budget < MINIMUM_DYNAMIC_CONTEXT_TOKENS:
            raise AgentRuntimePolicyError(
                code="agent_context_budget_exceeded",
                message="Agent instructions and tool contracts exceed the model input budget",
                event_type="agent.context.budget_exceeded",
                metadata={
                    "context_window_tokens": context_window,
                    "input_budget_tokens": input_budget,
                    "fixed_tokens": fixed_tokens,
                    "minimum_dynamic_context_tokens": MINIMUM_DYNAMIC_CONTEXT_TOKENS,
                },
            )

        selected: dict[int, str] = {}
        decisions: dict[int, ContextFragmentDecision] = {}
        remaining = dynamic_budget
        ordered = sorted(
            enumerate(fragments),
            key=lambda item: (
                not item[1].required,
                -int(item[1].priority),
                item[0],
            ),
        )
        for index, fragment in ordered:
            if not fragment.text:
                continue
            estimated = estimate_token_upper_bound(fragment.text)
            separator_tokens = 2 if selected else 0
            available = max(0, remaining - separator_tokens)
            included_text = ""
            status = "excluded"
            if estimated <= available:
                included_text = fragment.text
                status = "included"
            elif available > 0 and fragment.allow_truncation:
                included_text = truncate_to_token_bound(fragment.text, available)
                status = "truncated" if included_text else "excluded"
            included = estimate_token_upper_bound(included_text)
            if included_text:
                selected[index] = included_text
                remaining -= included + separator_tokens
            decisions[index] = ContextFragmentDecision(
                key=fragment.key,
                priority=fragment.priority.name.lower(),
                required=fragment.required,
                status=status,
                estimated_tokens=estimated,
                included_tokens=included,
            )

        rendered = "\n\n".join(selected[index] for index in sorted(selected)).strip()
        ordered_decisions = tuple(decisions[index] for index in sorted(decisions))
        return ContextBudgetResult(
            text=rendered,
            context_window_tokens=context_window,
            input_budget_tokens=input_budget,
            fixed_tokens=fixed_tokens,
            dynamic_budget_tokens=dynamic_budget,
            included_tokens=estimate_token_upper_bound(rendered),
            decisions=ordered_decisions,
        )


def context_window_tokens(
    provider: str | None,
    model: str | None,
    policy: ContextBudgetPolicy,
) -> int:
    capability = resolve_model_capability(provider, model)
    catalog_limit = capability.context_window_tokens if capability is not None else None
    configured = policy.context_window_tokens
    if catalog_limit is not None and configured is not None:
        return min(catalog_limit, configured)
    return catalog_limit or configured or DEFAULT_CONTEXT_WINDOW_TOKENS


def fixed_context_tokens(
    *,
    instructions: str,
    tool_definitions: tuple[AgentRuntimeToolDefinition, ...],
    continuations: tuple[AgentRuntimeToolContinuation, ...],
    handoff_agents: tuple[AgentRuntimeAgentDefinition, ...],
    agent_tools: tuple[AgentRuntimeAgentTool, ...],
    output_schema: AgentRuntimeOutputSchema | None,
) -> int:
    payload: dict[str, object] = {
        "instructions": instructions,
        "tools": tool_definitions,
        "continuations": continuations,
        "handoff_agents": handoff_agents,
        "agent_tools": agent_tools,
        "output_schema": output_schema,
    }
    return SERIALIZATION_OVERHEAD_TOKENS + estimate_token_upper_bound(
        json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True)
    )
