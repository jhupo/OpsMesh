"""Absent or normalized usage must not release an uncertain billing reservation."""

import pytest
from agents import ModelResponse
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
