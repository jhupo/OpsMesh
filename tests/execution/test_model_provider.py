"""Native SDK adapter receipts, output checks and failure accounting."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import ModelResponse
from agents.usage import InputTokensDetails, Usage

from opsmesh.orchestration.execution import model_provider
from opsmesh.orchestration.execution.contracts import ModelConnection
from opsmesh.orchestration.execution.model_provider import NativeModel, NativeModelProvider
from opsmesh.shared.errors import ConflictError


class Operations:
    def __init__(self, *, cached=None, hooks=False):
        self.cached = cached
        self.hooks = hooks
        self.completed = []
        self.finished = []
        self.claimed = []

    def has_hooks(self):
        return self.hooks

    async def before_hooks(self, args, kwargs, operation_id):
        return args, kwargs, ["check"] if self.hooks else []

    async def after_hooks(self, response, operation_id, entered):
        return response

    def claim(self, args, kwargs, operation_id, streaming):
        self.claimed.append((operation_id, streaming))
        return (
            self.cached is None,
            self.cached,
            ModelConnection("key", "http://localhost/v1", 1000),
            "test",
        )

    def complete(self, operation_id, result, usage):
        self.completed.append((operation_id, result, usage))

    async def finish_hooks(self, operation_id, entered, failed):
        self.finished.append((operation_id, entered, failed))


@pytest.fixture
def activity_boundary(monkeypatch):
    monkeypatch.setattr(
        model_provider.activity,
        "info",
        lambda: SimpleNamespace(workflow_id="flow-1", activity_id="model-1"),
    )


def test_provider_preserves_run_and_agent_reference():
    run_id = uuid4()
    references = []
    operations = Operations()

    def resolve(actual_id, agent_key):
        references.append((actual_id, agent_key))
        return operations

    provider = NativeModelProvider(resolve)
    assert isinstance(provider.get_model(f"{run_id}:analyst"), NativeModel)
    assert references == [(run_id, "analyst")]
    with pytest.raises(ValueError):
        provider.get_model(None)


def test_confirmed_response_replay_does_not_open_upstream(activity_boundary, monkeypatch):
    response = ModelResponse(
        output=[], usage=Usage(input_tokens=7, output_tokens=3), response_id="r"
    )
    operations = Operations(cached=model_provider.RESPONSE.dump_python(response, mode="json"))

    def forbidden_upstream(**kwargs):
        raise AssertionError("A replay must reuse its persisted response")

    monkeypatch.setattr(model_provider, "AsyncOpenAI", forbidden_upstream)
    result = asyncio.run(NativeModel(operations).get_response())
    assert result == response
    assert operations.claimed == [("flow-1:model-1", False)]
    assert not operations.completed
    assert operations.finished == [("flow-1:model-1", [], False)]


@pytest.mark.parametrize("failed", [False, True])
def test_native_io_accounts_actual_usage_or_preserves_uncertainty(
    activity_boundary, monkeypatch, failed
):
    response = ModelResponse(
        output=[],
        usage=Usage(
            input_tokens=100,
            output_tokens=10,
            input_tokens_details=InputTokensDetails(cached_tokens=20, cache_write_tokens=0),
        ),
        response_id="r",
    )

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "key"
            assert kwargs["max_retries"] == 0
            self.http = kwargs["http_client"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.http.aclose()

    class Provider:
        def __init__(self, **kwargs):
            pass

        def get_model(self, name):
            assert name == "test"
            return self

        async def get_response(self):
            if failed:
                raise TimeoutError("No provider confirmation")
            return response

    monkeypatch.setattr(model_provider, "AsyncOpenAI", Client)
    monkeypatch.setattr(model_provider, "OpenAIProvider", Provider)
    operations = Operations(hooks=True)
    model = NativeModel(operations)
    if failed:
        with pytest.raises(TimeoutError):
            asyncio.run(model.get_response())
        assert operations.completed == [("flow-1:model-1", None, None)]
    else:
        assert asyncio.run(model.get_response()) == response
        assert operations.completed[0][2] == {
            "input_tokens": 80,
            "cached_input_tokens": 20,
            "output_tokens": 10,
            "tool_invocations": 0,
        }
    assert operations.finished == [("flow-1:model-1", ["check"], failed)]


def test_streaming_cannot_bypass_installed_output_checks():
    operations = Operations(hooks=True)

    async def consume():
        return [event async for event in NativeModel(operations).stream_response()]

    with pytest.raises(ConflictError):
        asyncio.run(consume())
    assert not operations.claimed
    assert not operations.completed
