"""Absent or normalized usage must not release an uncertain billing reservation."""

from unittest.mock import Mock

import pytest
from agents import ModelResponse, ModelSettings, WebSearchTool, function_tool
from agents.models.interface import ModelTracing
from agents.usage import Usage

from opsmesh.execution.model import ExecutionModel
from opsmesh.shared.errors import InvalidArgumentError


@pytest.mark.parametrize(
    "raw_usage",
    [
        None,
        {},
        {"input_tokens": 7},
        {"input_tokens": 6, "output_tokens": 3},
        {"input_tokens": True, "output_tokens": 3},
        {"input_tokens": -1, "output_tokens": 3},
    ],
)
def test_missing_or_inconsistent_provider_usage_is_rejected(raw_usage):
    response = ModelResponse(
        output=[],
        usage=Usage(input_tokens=7, output_tokens=3),
        response_id="response",
        raw_usage=raw_usage,
    )
    with pytest.raises(InvalidArgumentError):
        ExecutionModel._validate_usage(response)


@pytest.mark.parametrize(
    "raw_usage",
    [{"input_tokens": 7, "output_tokens": 3}, {"prompt_tokens": 7, "completion_tokens": 3}],
)
def test_confirmed_responses_and_chat_usage_is_accepted(raw_usage):
    ExecutionModel._validate_usage(
        ModelResponse(
            output=[],
            usage=Usage(input_tokens=7, output_tokens=3),
            response_id="response",
            raw_usage=raw_usage,
        )
    )


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, "inference"),
        ({"previous_response_id": "previous"}, "model"),
        ({"conversation_id": "conversation"}, "model"),
        ({"prompt": {"id": "remote-prompt"}}, "model"),
        ({"tools": [WebSearchTool()]}, "model"),
        ({"model_settings": ModelSettings(max_tokens=100, extra_body={"tools": []})}, "model"),
    ],
)
def test_only_plain_model_calls_have_nonblocking_billing_receipts(overrides, expected):
    ledger = Mock()
    ledger.claim.return_value = None
    model = ExecutionModel(Mock(), ledger, context_tokens=10000, operation_id=lambda: "test")
    arguments = dict(
        system_instructions=None,
        input="Hello",
        model_settings=ModelSettings(max_tokens=100),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )
    model._prepare((), {**arguments, **overrides})
    assert ledger.claim.call_args.args[1] == expected


def test_local_function_calls_have_their_own_effect_receipts():
    @function_tool
    def example() -> str:
        return "ok"

    ledger = Mock()
    ledger.claim.return_value = None
    model = ExecutionModel(Mock(), ledger, operation_id=lambda: "test")
    model._prepare(
        (),
        dict(
            system_instructions=None,
            input="Hello",
            model_settings=ModelSettings(max_tokens=100),
            tools=[example],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        ),
    )
    assert ledger.claim.call_args.args[1] == "inference"
