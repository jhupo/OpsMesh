"""Verify native execution, callback boundaries, and resource ownership."""

import asyncio
from typing import Any

import pytest
from agents import Agent
from agents.models.interface import Model, ModelResponse
from agents.testing import ScriptedModel
from agents.testing.model import ModelStep, assistant_message
from agents.usage import Usage

import opsmesh.plugins.execution as execution_module
from opsmesh.plugins.execution import _lifecycle as lifecycle
from opsmesh.plugins.execution._lifecycle import ExecutionRuntime
from opsmesh.plugins.execution.config import Settings


def usage(input_tokens, output_tokens):
    return Usage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


@pytest.fixture
async def execution(monkeypatch, callback_owner):
    with callback_owner("execution"):
        pass
    with callback_owner("test_consumer"):
        pass
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
    from opsmesh.plugins import PluginStatus, get_plugin
    from opsmesh.plugins.invocation import drain_plugin

    consumer = get_plugin("cancelled")
    consumer.status = PluginStatus.STOPPING
    await drain_plugin(consumer, 0.2)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(observations) == 1
    assert isinstance(observations[0], asyncio.CancelledError)
    from opsmesh.plugins.invocation import PluginUnavailable

    with pytest.raises(PluginUnavailable):
        await execution.run(agent, "again", plugin_id="cancelled")


@pytest.mark.anyio
async def test_declared_dependency_callbacks_participate(
    execution, callback_owner, monkeypatch
):
    from opsmesh.plugins import get_plugin

    with callback_owner("chat"):
        pass
    with callback_owner("users"):
        pass
    get_plugin("chat").dependencies.append("users")
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
    interrupted = await execution.run(
        agent, "publish", plugin_id="test_consumer"
    )
    assert len(interrupted.interruptions) == 1
    assert invoked == []
    state = interrupted.to_state()
    state.approve(interrupted.interruptions[0])
    result = await execution.run(agent, state, plugin_id="test_consumer")
    assert result.final_output == "complete"
    assert invoked == [True]


@pytest.mark.anyio
async def test_cleanup_timeout_does_not_wait_for_cancellation_suppression():
    from opsmesh.plugins.execution._streaming import _pending_cleanup, _settle

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

    from opsmesh.plugins.execution._streaming import close_stream

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
def callback_owner(monkeypatch):
    from contextlib import contextmanager
    from types import ModuleType

    from opsmesh.plugins import Plugin, PluginManager, PluginStatus, registry

    owners = []
    monkeypatch.setattr(registry, "_plugins", {})

    @contextmanager
    def register(name):
        plugin = registry.get_plugin(name)
        if plugin is None:
            module = (
                execution_module if name == "execution" else ModuleType(name)
            )
            plugin = Plugin(name, module, module.__name__, PluginManager())
            plugin.status = PluginStatus.READY
            plugin.dependencies = [] if name == "execution" else ["execution"]
            registry._plugins[name] = plugin
            owners.append(plugin)
        token = registry._current_plugin.set(plugin)
        try:
            yield plugin
        finally:
            registry._current_plugin.reset(token)

    yield register
    for plugin in owners:
        for cleanup in reversed(plugin.activation_cleanups + plugin.cleanups):
            cleanup()


@pytest.mark.anyio
async def test_decorated_hooks_inject_context_and_keep_plugin_scopes(
    execution, callback_owner
):
    from typing import Annotated

    from opsmesh.params import Context, Depends

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

    from opsmesh.params import Depends

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

    from opsmesh.params import Depends

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
            agent, "hello", run_config=config, plugin_id="test_consumer"
        ) as result:
            async for _ in result.stream_events():
                pass
    else:
        result = await execution.run(
            agent, "hello", run_config=config, plugin_id="test_consumer"
        )
    assert result.final_output == "sandbox answer"
    sandbox.assert_complete()


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_callback_cleanup_failure_reaches_caller(
    execution, callback_owner, streaming
):
    from typing import Annotated

    from opsmesh.params import Depends

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

    for cleanup in tuple(plugin.activation_cleanups + plugin.cleanups):
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

    from opsmesh.params import Depends

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


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_admitted_callbacks_continue_while_owner_stops(
    execution, callback_owner, streaming
):
    from typing import Annotated

    from opsmesh.params import Depends
    from opsmesh.plugins import PluginStatus, get_plugin
    from opsmesh.plugins.invocation import PluginUnavailable, plugin_call

    observed = []

    async def resource():
        async with plugin_call("stopping"):
            observed.append("open")
        try:
            yield None
        finally:
            async with plugin_call("stopping"):
                observed.append("close")

    with callback_owner("stopping"):

        @execution.on_llm_start
        async def stopping():
            owner = get_plugin("stopping")
            assert owner.calls
            owner.status = PluginStatus.STOPPING

        @execution.on_llm_end
        async def response(value: Annotated[Any, Depends(resource)]):
            async with plugin_call("stopping"):
                observed.append("response")

            async def background():
                with pytest.raises(PluginUnavailable):
                    async with plugin_call("stopping"):
                        pass

            await asyncio.create_task(background())

        @execution.on_run_end
        async def finished(value: Annotated[Any, Depends(resource)]):
            async with plugin_call("stopping"):
                observed.append("finished")

    model = ScriptedModel([ModelStep(output=[assistant_message("done")])])
    agent = Agent(name="test", model=model)
    if streaming:
        async with execution.stream(
            agent, "hello", plugin_id="stopping"
        ) as result:
            async for _ in result.stream_events():
                pass
    else:
        result = await execution.run(agent, "hello", plugin_id="stopping")
    assert result.final_output == "done"
    assert observed == [
        "open",
        "response",
        "close",
        "open",
        "finished",
        "close",
    ]
    assert not get_plugin("stopping").calls


@pytest.mark.anyio
async def test_callback_dependencies_and_finalizers_keep_their_owner(
    execution, callback_owner
):
    from typing import Annotated

    from opsmesh.params import Depends
    from opsmesh.plugins import get_current_plugin, get_plugin

    observed = []

    async def resource():
        identifier = get_current_plugin().id_
        observed.append(("open", identifier))
        try:
            yield identifier
        finally:
            observed.append(("close", get_current_plugin().id_))

    for identifier in ("consumer", "observer"):
        with callback_owner(identifier):

            @execution.on_llm_start
            async def observe(value: Annotated[str, Depends(resource)]):
                assert get_current_plugin().id_ == value

    get_plugin("consumer").dependencies.append("observer")
    model = ScriptedModel([ModelStep(output=[assistant_message("done")])])
    await execution.run(
        Agent(name="test", model=model), "hello", plugin_id="consumer"
    )
    assert observed == [
        ("open", "consumer"),
        ("open", "observer"),
        ("close", "observer"),
        ("close", "consumer"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("event", ["on_llm_end", "on_run_end"])
async def test_callback_cleanup_timeout_retains_its_plugin_admission(
    execution, callback_owner, monkeypatch, event
):
    from typing import Annotated

    from opsmesh.params import Depends
    from opsmesh.plugins import get_plugin
    from opsmesh.plugins.invocation import drain_plugin

    started = asyncio.Event()
    finished = asyncio.Event()

    async def resource():
        try:
            yield None
        finally:
            started.set()
            await asyncio.sleep(1)
            finished.set()

    with callback_owner("cleanup_timeout"):

        @getattr(execution, event)
        async def observe(value: Annotated[Any, Depends(resource)]):
            pass

    monkeypatch.setattr(
        lifecycle.get_runtime(),
        "settings",
        Settings(cleanup_timeout_seconds=0.01),
    )
    agent = Agent(
        name="test",
        model=ScriptedModel([ModelStep(output=[assistant_message("done")])]),
    )
    if event == "on_llm_end":
        with pytest.raises(TimeoutError):
            await execution.run(agent, "hello", plugin_id="cleanup_timeout")
    else:
        result = await execution.run(
            agent, "hello", plugin_id="cleanup_timeout"
        )
        assert result.final_output == "done"
    owner = get_plugin("cleanup_timeout")
    assert started.is_set()
    assert not finished.is_set()
    assert owner.calls
    assert all(call.active for call in owner.calls.values())
    assert get_plugin("execution").calls
    with pytest.raises(TimeoutError):
        await drain_plugin(owner, 0.01)


@pytest.mark.anyio
async def test_background_execution_requires_explicit_known_owner(execution):
    model = ScriptedModel([ModelStep(output=[assistant_message("unused")])])
    agent = Agent(name="test", model=model)
    with pytest.raises(ValueError, match="requires plugin_id"):
        await execution.run(agent, "hello")
    with pytest.raises(ValueError, match="Unknown execution owner"):
        await execution.run(agent, "hello", plugin_id="missing")
    assert not model.calls


@pytest.mark.anyio
async def test_cancelled_future_in_end_observer_propagates(
    execution, callback_owner
):
    with callback_owner("terminal_future"):

        @execution.on_run_end
        async def cancelled():
            future = asyncio.get_running_loop().create_future()
            future.cancel()
            await future

    model = ScriptedModel([ModelStep(output=[assistant_message("done")])])
    with pytest.raises(asyncio.CancelledError):
        await execution.run(
            Agent(name="test", model=model),
            "hello",
            plugin_id="terminal_future",
        )


@pytest.mark.anyio
async def test_pending_sdk_cleanup_keeps_plugin_resources(
    execution, callback_owner, monkeypatch
):

    from opsmesh.plugins import get_plugin
    from opsmesh.plugins.execution import _invocation

    class PendingStream:
        is_complete = True
        run_loop_exception = None
        run_loop_task = None

    released = asyncio.Event()
    closing = asyncio.Event()
    result = PendingStream()
    result._sandbox_cleanup_task = asyncio.create_task(released.wait())

    async def timeout_cleanup(*args, **kwargs):
        closing.set()
        return False

    monkeypatch.setattr(_invocation, "RunResultStreaming", PendingStream)
    monkeypatch.setattr(_invocation, "close_stream", timeout_cleanup)
    with callback_owner("pending"):
        pass

    async def consume():
        async with _invocation.Invocation(
            Agent(name="unused"),
            "hello",
            context=None,
            hooks=None,
            run_config=None,
            plugin_id="pending",
            trace_context=None,
        ) as invocation:
            invocation.result = result

    task = asyncio.create_task(consume())
    try:
        await closing.wait()
        await asyncio.sleep(0)
        assert get_plugin("pending").calls
        assert get_plugin("execution").calls
        assert not task.done()
    finally:
        released.set()
        with pytest.raises(TimeoutError, match="SDK stream cleanup failed"):
            await task
    assert not get_plugin("pending").calls


@pytest.mark.anyio
@pytest.mark.parametrize("guardrail_type", ["input", "output"])
async def test_native_stream_guardrails_keep_plugin_resources_until_done(
    execution, callback_owner, monkeypatch, guardrail_type
):
    from agents import (
        GuardrailFunctionOutput,
        input_guardrail,
        output_guardrail,
    )

    from opsmesh.plugins import get_plugin
    from opsmesh.plugins.execution import _invocation

    entered = asyncio.Event()
    released = asyncio.Event()
    settling = asyncio.Event()
    guardrail_tasks = []

    async def guardrail(context, agent, value):
        guardrail_tasks.append(asyncio.current_task())
        entered.set()
        while not released.is_set():
            try:
                await released.wait()
            except asyncio.CancelledError:
                pass
        return GuardrailFunctionOutput(
            output_info=None, tripwire_triggered=False
        )

    native_settle = _invocation.settle_stream

    async def settle(result):
        settling.set()
        await native_settle(result)

    monkeypatch.setattr(_invocation, "settle_stream", settle)
    monkeypatch.setattr(
        lifecycle.get_runtime(),
        "settings",
        Settings(cleanup_timeout_seconds=0.01),
    )
    with callback_owner("guarded"):
        pass
    decorator = (
        input_guardrail if guardrail_type == "input" else output_guardrail
    )
    agent = Agent(
        name="guarded",
        model=ScriptedModel([ModelStep(output=[assistant_message("done")])]),
        **{f"{guardrail_type}_guardrails": [decorator(guardrail)]},
    )
    results = []

    async def consume():
        async with execution.stream(
            agent, "hello", plugin_id="guarded"
        ) as result:
            results.append(result)
            await entered.wait()

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(settling.wait(), timeout=10)
        assert not consumer.done()
        assert all(not task.done() for task in guardrail_tasks)
        assert get_plugin("guarded").calls
        assert get_plugin("execution").calls
        guardrail_task = getattr(
            results[0], f"_{guardrail_type}_guardrails_task"
        )
        assert not guardrail_task.done()
    finally:
        released.set()
        with pytest.raises(TimeoutError, match="SDK stream cleanup failed"):
            await asyncio.wait_for(consumer, timeout=5)
    assert all(task.done() for task in guardrail_tasks)
    assert guardrail_task.done()
    assert not get_plugin("guarded").calls
    assert not get_plugin("execution").calls


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_sdk_agent_tool_continues_admitted_run_during_stop(
    execution, callback_owner, streaming
):
    from agents.testing import function_call

    from opsmesh.plugins import PluginStatus, get_plugin

    with callback_owner("delegate"):

        @execution.on_tool_start
        async def stop_owner(tool):
            get_plugin("delegate").status = PluginStatus.STOPPING

    inner = Agent(
        name="inner",
        model=ScriptedModel(
            [ModelStep(output=[assistant_message("inner done")])]
        ),
    )

    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "delegate", '{"input":"nested"}', call_id="delegate-1"
                    )
                ]
            ),
            ModelStep(output=[assistant_message("outer done")]),
        ]
    )
    outer = Agent(
        name="outer",
        model=model,
        tools=[inner.as_tool("delegate", "Delegate to the inner agent")],
    )
    if streaming:
        async with execution.stream(
            outer, "start", plugin_id="delegate"
        ) as result:
            async for _ in result.stream_events():
                pass
    else:
        result = await execution.run(outer, "start", plugin_id="delegate")
    assert result.final_output == "outer done"
    assert len(inner.model.calls) == 1
    assert not get_plugin("delegate").calls


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_child_execution_cannot_inherit_stopping_parent_admission(
    execution, callback_owner, streaming
):
    from opsmesh.plugins import PluginStatus, get_plugin
    from opsmesh.plugins.invocation import PluginUnavailable

    child_model = ScriptedModel(
        [ModelStep(output=[assistant_message("unused")])]
    )
    child = Agent(name="child", model=child_model)
    rejected = []

    with callback_owner("independent"):

        @execution.before_run
        async def start_background(agent):
            if agent.name != "parent":
                return

            async def background():
                with pytest.raises(PluginUnavailable):
                    if streaming:
                        async with execution.stream(
                            child, "new", plugin_id="independent"
                        ):
                            pass
                    else:
                        await execution.run(
                            child, "new", plugin_id="independent"
                        )
                rejected.append(True)

            task = asyncio.create_task(background())
            get_plugin("independent").status = PluginStatus.STOPPING
            await task

    result = await execution.run(
        Agent(
            name="parent",
            model=ScriptedModel(
                [ModelStep(output=[assistant_message("parent done")])]
            ),
        ),
        "hello",
        plugin_id="independent",
    )
    assert result.final_output == "parent done"
    assert rejected == [True]
    assert not child_model.calls
    assert not get_plugin("independent").calls
