import pytest

from backend.app.agent_runtime.errors import AgentRuntimePolicyError
from backend.app.agent_runtime.token_estimation import estimate_token_upper_bound
from backend.app.agents.memory_policy import ContextBudgetPolicy
from backend.app.agents.payloads import normalize_create_payload
from backend.app.orchestration.context_budget import (
    ContextBudgetManager,
    ContextFragment,
    ContextPriority,
)


def test_context_budget_keeps_required_context_and_truncates_by_priority() -> None:
    result = ContextBudgetManager().build(
        fragments=(
            ContextFragment(
                key="task.objective",
                text="A" * 1_200,
                priority=ContextPriority.CRITICAL,
                required=True,
            ),
            ContextFragment(
                key="runtime.capabilities",
                text="B" * 1_200,
                priority=ContextPriority.HIGH,
            ),
            ContextFragment(
                key="task.completed_steps",
                text="C" * 1_200,
                priority=ContextPriority.LOW,
            ),
        ),
        provider="openai-compatible",
        model="custom-model",
        policy=ContextBudgetPolicy(
            context_window_tokens=4_096,
            max_input_tokens=2_048,
            output_reserve_tokens=256,
            safety_margin_tokens=128,
        ),
        instructions="Work safely.",
    )

    decisions = {item.key: item for item in result.decisions}
    assert decisions["task.objective"].status == "included"
    assert decisions["runtime.capabilities"].status == "truncated"
    assert decisions["task.completed_steps"].status == "excluded"
    assert estimate_token_upper_bound(result.text) <= result.dynamic_budget_tokens
    assert result.fixed_tokens + result.included_tokens <= result.input_budget_tokens
    assert result.evidence()["estimator"] == "utf8_bytes_upper_bound"


def test_context_budget_fails_closed_when_fixed_contracts_leave_no_task_budget() -> None:
    with pytest.raises(AgentRuntimePolicyError) as exc_info:
        ContextBudgetManager().build(
            fragments=(
                ContextFragment(
                    key="task.objective",
                    text="Do the task",
                    priority=ContextPriority.CRITICAL,
                    required=True,
                ),
            ),
            provider="openai-compatible",
            model="custom-model",
            policy=ContextBudgetPolicy(
                context_window_tokens=4_096,
                max_input_tokens=1_024,
                output_reserve_tokens=256,
                safety_margin_tokens=128,
            ),
            instructions="X" * 2_000,
        )

    assert exc_info.value.code == "agent_context_budget_exceeded"
    assert exc_info.value.retryable is False


def test_agent_profiles_receive_a_complete_default_context_budget() -> None:
    values = normalize_create_payload({"name": "Analyst", "role": "worker"}, {})

    assert values["memory_policy"] == {
        "context_budget": {
            "max_input_tokens": None,
            "context_window_tokens": None,
            "output_reserve_tokens": 4_096,
            "safety_margin_tokens": 1_024,
        },
        "working_memory": {
            "enabled": True,
            "ttl_seconds": 86_400,
            "max_entries": 64,
            "max_entry_tokens": 2_048,
        },
    }


def test_context_budget_rejects_unknown_or_invalid_agent_configuration() -> None:
    with pytest.raises(ValueError, match="Invalid agent context budget policy"):
        normalize_create_payload(
            {
                "name": "Analyst",
                "role": "worker",
                "memory_policy": {"context_budget": {"enabled": False}},
            },
            {},
        )
