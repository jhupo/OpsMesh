"""Verify native execution, callback boundaries, and resource ownership."""

import asyncio
from typing import Any

import pytest
from agents import Agent
from agents.models.interface import Model, ModelResponse
from agents.testing import ScriptedModel
from agents.testing.model import ModelStep, assistant_message
from agents.usage import Usage

import opsmesh_execution as execution_module
from opsmesh_execution import _lifecycle as lifecycle
from opsmesh_execution._lifecycle import ExecutionRuntime
from opsmesh_execution.config import Settings


def usage(input_tokens, output_tokens):
    return Usage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


@pytest.fixture
async def execution(monkeypatch):
    runtime = ExecutionRuntime(Settings())
    await runtime.startup()
    monkeypatch.setattr(lifecycle, "_runtime", runtime)
    try:
        yield execution_module
    finally:
        await runtime.shutdown()


class BlockingModel(Model):
    """Wait forever so tests can cancel an active run."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def get_response(
        self,
        system_instructions: Any,
        input: Any,
        model_settings: Any,
        tools: Any,
        output_schema: Any,
        handoffs: Any,
        tracing: Any,
        *,
        previous_response_id: Any,
        conversation_id: Any,
        prompt: Any,
    ) -> ModelResponse:
        """Signal the test and wait for cancellation."""
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def stream_response(self, *args: Any, **kwargs: Any):
        """Never used by non-streaming tests."""
        self.started.set()
        await asyncio.Event().wait()
        if False:
            yield None


@pytest.mark.anyio
async def test_before_run_rejection_never_calls_model(
    execution, callback_owner
):
    from agents import RunResult

    rejected = ValueError("Quota exhausted")
    observations = []
    with callback_owner("limits"):

        @execution.before_run
        async def check(agent, input):
            raise rejected

        @execution.on_run_end
        async def observe(
            result: RunResult | None, error: BaseException | None
        ):
            observations.append((result, error))

    model = ScriptedModel([ModelStep(output=[assistant_message("unused")])])
    with pytest.raises(ValueError) as caught:
        await execution.run(
            Agent(name="test", model=model), "hello", plugin_id="limits"
        )
    assert caught.value is rejected
    assert not model.calls
    assert observations == [(None, rejected)]


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_terminal_observer_failure_preserves_native_result(
    execution, callback_owner, streaming
):
    observations = []
    with callback_owner("statistics"):

        @execution.on_run_end
        async def unavailable(result, error):
            raise RuntimeError("Statistics storage unavailable")

        @execution.on_run_end
        async def observe(result, error):
            observations.append((result, error))

    model = ScriptedModel(
        [ModelStep(output=[assistant_message("answer")], usage=usage(2, 3))]
    )
    agent = Agent(name="test", model=model)
    if streaming:
        async with execution.stream(
            agent, "hello", plugin_id="statistics"
        ) as result:
            async for _ in result.stream_events():
                pass
    else:
        result = await execution.run(agent, "hello", plugin_id="statistics")
    assert observations[0][0] is result
    assert observations[0][1] is None
    assert result.final_output == "answer"
    assert result.context_wrapper.usage.total_tokens == 5
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_cancellation_during_terminal_observation_reaches_caller(
    execution, callback_owner
):
    entered = asyncio.Event()
    notified = []
    with callback_owner("terminal"):

        @execution.on_run_end
        async def slow_observer():
            entered.set()
            await asyncio.Event().wait()

        @execution.on_run_end
        async def next_observer():
            notified.append(True)

    model = ScriptedModel([ModelStep(output=[assistant_message("answer")])])
    task = asyncio.create_task(
        execution.run(
            Agent(name="test", model=model), "hello", plugin_id="terminal"
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert notified == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_shutdown_cancels_consumers_and_notifies_observers(
    execution, callback_owner, streaming
):
    from opsmesh_execution.errors import ExecutionClosed

    observations = []
    with callback_owner("cancelled"):

        @execution.on_run_end
        async def observe(result, error):
            observations.append(error)

    model = BlockingModel()
    agent = Agent(name="test", model=model)

    async def consume():
        if streaming:
            async with execution.stream(
                agent, "hello", plugin_id="cancelled"
            ) as result:
                async for _ in result.stream_events():
                    pass
        else:
            await execution.run(agent, "hello", plugin_id="cancelled")

    task = asyncio.create_task(consume())
    await asyncio.wait_for(model.started.wait(), 2)
    await lifecycle.get_runtime().shutdown()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(observations) == 1
    assert isinstance(observations[0], asyncio.CancelledError)
    with pytest.raises(ExecutionClosed):
        await execution.run(agent, "again")


@pytest.mark.anyio
async def test_declared_dependency_callbacks_participate(
    execution, callback_owner, monkeypatch
):
    from types import SimpleNamespace

    from opsmesh_execution import callbacks

    dependencies = {
        "chat": SimpleNamespace(dependencies={"users"}),
        "users": SimpleNamespace(dependencies=set()),
    }
    monkeypatch.setattr(callbacks, "get_plugin", dependencies.get)
    observed = []
    with callback_owner("users"):

        @execution.before_run
        async def reserve():
            observed.append("reserve")

        @execution.on_run_end
        async def settle(result):
            observed.append(result.final_output)

    model = ScriptedModel([ModelStep(output=[assistant_message("answer")])])
    await execution.run(
        Agent(name="test", model=model), "hello", plugin_id="chat"
    )
    assert observed == ["reserve", "answer"]


@pytest.mark.anyio
async def test_native_approval_state_is_resumed_without_platform_state(
    execution,
):
    from agents import function_tool
    from agents.testing import function_call

    invoked = []

    @function_tool(needs_approval=True)
    def publish() -> str:
        """Publish the approved content."""
        invoked.append(True)
        return "published"

    model = ScriptedModel(
        [
            ModelStep(
                output=[function_call("publish", "{}", call_id="publish-1")]
            ),
            ModelStep(output=[assistant_message("complete")]),
        ]
    )
    agent = Agent(name="test", model=model, tools=[publish])
    interrupted = await execution.run(agent, "publish")
    assert len(interrupted.interruptions) == 1
    assert invoked == []
    state = interrupted.to_state()
    state.approve(interrupted.interruptions[0])
    result = await execution.run(agent, state)
    assert result.final_output == "complete"
    assert invoked == [True]


@pytest.mark.anyio
async def test_cleanup_timeout_does_not_wait_for_cancellation_suppression():
    from opsmesh_execution._streaming import _pending_cleanup, _settle

    released = asyncio.Event()

    async def resistant_task():
        while not released.is_set():
            try:
                await released.wait()
            except asyncio.CancelledError:
                continue

    task = asyncio.create_task(resistant_task())
    await asyncio.sleep(0)
    task.cancel()
    cleanup = asyncio.create_task(_settle(task))
    try:
        _, pending = await asyncio.wait({cleanup}, timeout=1.5)
        assert not pending
        assert task in _pending_cleanup
    finally:
        released.set()
        await task
        await cleanup


@pytest.mark.anyio
async def test_stream_cleanup_finishes_before_consumer_cancellation():
    from types import SimpleNamespace

    from opsmesh_execution._streaming import close_stream

    entered = asyncio.Event()
    release = asyncio.Event()

    async def sdk_cleanup():
        entered.set()
        await release.wait()

    sdk_task = asyncio.create_task(sdk_cleanup())
    result = SimpleNamespace(run_loop_task=None, _sandbox_cleanup_task=sdk_task)
    closing = asyncio.create_task(close_stream(result, 2))
    await entered.wait()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    assert not sdk_task.cancelled()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert sdk_task.done()


@pytest.fixture
def callback_owner():
    from contextlib import contextmanager
    from types import SimpleNamespace

    from opsmesh.framework.plugins import registry

    owners = []

    @contextmanager
    def register(name):
        plugin = SimpleNamespace(id_=name, ready=True, cleanups=[])
        owners.append(plugin)
        token = registry._current_plugin.set(plugin)
        try:
            yield plugin
        finally:
            registry._current_plugin.reset(token)

    yield register
    for plugin in owners:
        for cleanup in reversed(plugin.cleanups):
            cleanup()


@pytest.mark.anyio
async def test_decorated_hooks_inject_context_and_keep_plugin_scopes(
    execution, callback_owner
):
    from typing import Annotated

    from opsmesh.framework.params import Context, Depends

    events = []
    caller = {"id": "trusted-user"}

    async def resource():
        events.append("opened")
        try:
            yield "shared"
        finally:
            events.append("closed")

    with callback_owner("first"):

        @execution.on_llm_start
        async def before(value: Annotated[str, Depends(resource)]):
            events.append(value)

        @execution.on_llm_end
        async def after(
            response: ModelResponse,
            user: Annotated[Any, Context()],
            value: Annotated[str, Depends(resource)],
        ):
            assert isinstance(response.usage, Usage)
            assert user is caller
            events.append((value, response.usage.total_tokens))

    with callback_owner("second"):

        @execution.on_llm_end
        async def foreign(response):
            raise AssertionError("Another plugin observed this model response")

    model = ScriptedModel(
        [ModelStep(output=[assistant_message("done")], usage=usage(2, 3))]
    )
    result = await execution.run(
        Agent(name="test", model=model),
        "hello",
        context=caller,
        plugin_id="first",
    )
    assert result.final_output == "done"
    assert events == ["opened", "shared", ("shared", 5), "closed"]


@pytest.mark.anyio
async def test_decorated_hook_failure_closes_dependency_without_replay(
    execution, callback_owner
):
    from typing import Annotated

    from opsmesh.framework.params import Depends

    failures = []

    async def resource():
        try:
            yield None
        except ValueError as error:
            failures.append(str(error))
            raise

    with callback_owner("failure"):

        @execution.on_llm_end
        async def fail(response, resource: Annotated[Any, Depends(resource)]):
            raise ValueError("callback failed")

    model = ScriptedModel([ModelStep(output=[assistant_message("once")])])
    with pytest.raises(ValueError, match="callback failed"):
        await execution.run(
            Agent(name="test", model=model),
            "hello",
            plugin_id="failure",
        )
    assert len(model.calls) == 1
    assert failures == ["callback failed"]


@pytest.mark.anyio
async def test_stream_keeps_callback_dependencies_until_exit(
    execution, callback_owner
):
    from typing import Annotated

    from opsmesh.framework.params import Depends

    lifecycle = []

    async def resource():
        lifecycle.append("open")
        try:
            yield None
        finally:
            lifecycle.append("close")

    with callback_owner("streaming"):

        @execution.on_agent_start
        async def start(resource: Annotated[Any, Depends(resource)]):
            lifecycle.append("start")

    model = ScriptedModel([ModelStep(output=[assistant_message("done")])])
    async with execution.stream(
        Agent(name="test", model=model),
        "hello",
        plugin_id="streaming",
    ) as result:
        async for _event in result.stream_events():
            if lifecycle:
                break
        assert "close" not in lifecycle
    assert lifecycle == ["open", "start", "close"]


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_native_sandbox_runs_through_same_execution(execution, streaming):
    from agents import RunConfig
    from agents.sandbox import SandboxAgent, SandboxRunConfig
    from agents.testing import scripted_sandbox_session

    model = ScriptedModel(
        [ModelStep(output=[assistant_message("sandbox answer")])]
    )
    agent = SandboxAgent(name="sandbox", model=model, capabilities=[])
    sandbox = scripted_sandbox_session([])
    config = RunConfig(
        sandbox=SandboxRunConfig(session=sandbox), tracing_disabled=True
    )
    if streaming:
        async with execution.stream(
            agent, "hello", run_config=config
        ) as result:
            async for _ in result.stream_events():
                pass
    else:
        result = await execution.run(agent, "hello", run_config=config)
    assert result.final_output == "sandbox answer"
    sandbox.assert_complete()


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_callback_cleanup_failure_reaches_caller(
    execution, callback_owner, streaming
):
    from typing import Annotated

    from opsmesh.framework.params import Depends

    async def resource():
        yield None
        raise ValueError("callback cleanup failed")

    with callback_owner("cleanup"):

        @execution.on_llm_start
        async def before(resource: Annotated[Any, Depends(resource)]):
            pass

    model = ScriptedModel([ModelStep(output=[assistant_message("done")])])
    agent = Agent(name="test", model=model)
    plugin_id = "cleanup"
    with pytest.raises(ValueError, match="callback cleanup failed"):
        if streaming:
            async with execution.stream(
                agent, "hello", plugin_id=plugin_id
            ) as result:
                async for _ in result.stream_events():
                    pass
        else:
            await execution.run(agent, "hello", plugin_id=plugin_id)


@pytest.mark.anyio
async def test_revoked_callback_is_not_selected(execution, callback_owner):
    with callback_owner("revoked") as plugin:

        @execution.on_llm_end
        async def revoked(response):
            raise AssertionError("Revoked callback ran")

    for cleanup in plugin.cleanups:
        cleanup()
    model = ScriptedModel([ModelStep(output=[assistant_message("done")])])
    result = await execution.run(
        Agent(name="test", model=model),
        "hello",
        plugin_id="revoked",
    )
    assert result.final_output == "done"


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_callback_cleanup_preserves_original_failure(
    execution, callback_owner, streaming, cancelled
):
    from typing import Annotated

    from opsmesh.framework.params import Depends

    original = (
        asyncio.CancelledError("execution cancelled")
        if cancelled
        else ValueError("callback failed")
    )
    closed = []

    async def resource():
        try:
            yield None
        finally:
            closed.append(True)
            raise RuntimeError("dependency close failed")

    with callback_owner("failure"):

        @execution.on_llm_start
        async def fail(resource: Annotated[Any, Depends(resource)]):
            if not (streaming and cancelled):
                raise original

    agent = Agent(
        name="test",
        model=ScriptedModel([ModelStep(output=[assistant_message("unused")])]),
    )
    plugin_id = "failure"
    with pytest.raises(type(original)) as caught:
        if streaming:
            async with execution.stream(
                agent, "hello", plugin_id=plugin_id
            ) as result:
                async for _ in result.stream_events():
                    pass
                if cancelled:
                    raise original
        else:
            await execution.run(agent, "hello", plugin_id=plugin_id)
    assert caught.value is original
    assert closed == [True]
    assert any("dependency close failed" in note for note in original.__notes__)
