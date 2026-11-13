"""Exercise dependency admission and ownership across asynchronous tasks."""

import asyncio
from contextlib import AsyncExitStack
from types import ModuleType
from typing import Annotated

import anyio
import pytest

from opsmesh.plugins import registry
from opsmesh.plugins.invocation import (
    PluginUnavailable,
    continued_call,
    hold_continuation,
    hold_plugin,
    plugin_call,
)
from opsmesh.plugins.model import Plugin, PluginStatus


@pytest.fixture
def plugins(monkeypatch):
    provider = Plugin("provider", ModuleType("provider"), "provider", None)
    caller = Plugin("caller", ModuleType("caller"), "caller", None)
    provider.status = caller.status = PluginStatus.READY
    caller.dependencies = [provider.id_]
    monkeypatch.setattr(
        registry, "_plugins", {item.id_: item for item in (provider, caller)}
    )
    return provider, caller


@pytest.mark.anyio
async def test_unavailable_dependency_does_not_partially_admit(plugins):
    provider, caller = plugins
    provider.status = PluginStatus.STOPPING
    async with AsyncExitStack() as stack:
        with pytest.raises(PluginUnavailable):
            hold_plugin(caller, stack)
        assert not caller.calls and not provider.calls


@pytest.mark.anyio
async def test_nested_call_can_finish_after_stop_admission(plugins):
    provider, caller = plugins
    async with plugin_call(caller.id_) as original:
        provider.status = caller.status = PluginStatus.STOPPING
        async with plugin_call(caller.id_) as nested:
            assert nested is original
    assert not provider.calls and not caller.calls


@pytest.mark.anyio
async def test_child_task_cannot_borrow_stopping_parent_call(plugins):
    provider, caller = plugins
    async with plugin_call(caller.id_):
        caller.status = PluginStatus.STOPPING

        async def background():
            async with plugin_call(caller.id_):
                pytest.fail("A child task borrowed its parent's admission")

        with pytest.raises(PluginUnavailable):
            await asyncio.create_task(background())


@pytest.mark.anyio
async def test_expired_inherited_context_cannot_reopen_resources(plugins):
    _, caller = plugins
    proceed = asyncio.Event()

    async def background():
        await proceed.wait()
        async with plugin_call(caller.id_):
            pytest.fail("An expired call was reused")

    async with plugin_call(caller.id_):
        task = asyncio.create_task(background())
    proceed.set()
    with pytest.raises(PluginUnavailable, match="ended"):
        await task


@pytest.mark.anyio
async def test_continuation_survives_parent_and_stopping(plugins):
    provider, caller = plugins
    entered = asyncio.Event()
    release = asyncio.Event()

    async def continuation(origin):
        async with continued_call(origin, provider.id_) as call:
            entered.set()
            await release.wait()
            assert call.active

    async with plugin_call(caller.id_) as parent:
        provider.status = caller.status = PluginStatus.STOPPING
        task = asyncio.create_task(continuation(parent))
        await entered.wait()
    assert not parent.active and provider.calls and not caller.calls
    release.set()
    await task
    assert not provider.calls


@pytest.mark.anyio
async def test_continuation_rejects_expired_origin(plugins):
    provider, caller = plugins
    async with plugin_call(caller.id_) as parent:
        pass
    with pytest.raises(PluginUnavailable, match="ended"):
        async with continued_call(parent, provider.id_):
            pytest.fail("Expired continuation was admitted")
    assert not provider.calls


@pytest.mark.anyio
async def test_continuation_cannot_extend_dependency_closure(plugins):
    provider, caller = plugins
    async with plugin_call(provider.id_) as parent:
        with pytest.raises(PluginUnavailable, match="exceeds"):
            async with continued_call(parent, caller.id_):
                pytest.fail("Unadmitted dependency was added")
        assert not caller.calls


@pytest.mark.anyio
async def test_drain_waits_for_continuations_created_after_its_snapshot(
    plugins,
):
    from opsmesh.plugins.invocation import drain_plugin

    provider, caller = plugins
    parent_entered = asyncio.Event()
    continue_now = asyncio.Event()
    pinned = asyncio.Event()
    release = asyncio.Event()
    parent_left = asyncio.Event()

    async def continuation(origin):
        async with continued_call(origin, provider.id_):
            pinned.set()
            await release.wait()

    async def run():
        async with plugin_call(caller.id_) as parent:
            parent_entered.set()
            await continue_now.wait()
            task = asyncio.create_task(continuation(parent))
            await pinned.wait()
        parent_left.set()
        await task

    running = asyncio.create_task(run())
    await parent_entered.wait()
    draining = asyncio.create_task(drain_plugin(provider, 2))
    await asyncio.sleep(0)
    provider.status = PluginStatus.STOPPING
    continue_now.set()
    await parent_left.wait()
    await asyncio.sleep(0)
    assert not draining.done()
    release.set()
    await running
    await draining
    assert not provider.calls


def test_scope_controls_synchronous_owner(plugins):
    _, caller = plugins
    with registry.plugin_scope(caller):
        assert registry.get_current_plugin() is caller


def test_sync_owner_uses_loaded_plugin_in_opsmesh_namespace(monkeypatch):
    module = ModuleType("opsmesh.plugins.custom")
    module.registry = registry
    exec(
        "def owner():\n    return registry.get_current_plugin()\n",
        module.__dict__,
    )
    plugin = Plugin("custom", module, module.__name__, None)
    monkeypatch.setattr(registry, "_plugins", {plugin.id_: plugin})
    assert module.owner() is plugin


def test_sync_provider_resolves_its_actual_dependent(monkeypatch):
    provider_module = ModuleType("opsmesh.plugins.foundation")
    provider_module.registry = registry
    exec(
        "def lookup():\n    return registry.get_current_plugin()\n",
        provider_module.__dict__,
    )
    provider = Plugin(
        "foundation", provider_module, provider_module.__name__, None
    )
    caller_module = ModuleType("opsmesh.plugins.custom")
    caller_module.provider = provider_module
    exec(
        "def lookup():\n    return provider.lookup()\n", caller_module.__dict__
    )
    caller = Plugin("custom", caller_module, caller_module.__name__, None)
    caller.dependencies = [provider.id_]
    monkeypatch.setattr(
        registry, "_plugins", {item.id_: item for item in (provider, caller)}
    )
    assert caller_module.lookup() is caller


@pytest.mark.anyio
async def test_false_matcher_releases_owner_before_event_stack(
    plugins, conversation
):
    from opsmesh.dispatch.execution import check_and_run_matcher
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.params import Depends
    from opsmesh.rules import Rule

    _, caller = plugins
    closed = []

    async def resource():
        try:
            yield True
        finally:
            closed.append(registry.get_current_plugin())

    dependency = Depends(resource)

    async def reject(value=dependency):
        return False

    matcher = Matcher.new(
        rule=Rule(reject),
        source=MatcherSource(plugin_id=caller.id_, module_name="caller"),
    )
    try:
        async with AsyncExitStack() as event_stack:
            await check_and_run_matcher(
                matcher, *conversation, {}, stack=event_stack
            )
            assert not caller.calls and closed == [caller]
    finally:
        matcher.destroy()


@pytest.mark.anyio
async def test_cancelled_matcher_check_closes_with_correct_owner(
    plugins, conversation, monkeypatch
):
    from opsmesh.dispatch import execution
    from opsmesh.matchers import Matcher, MatcherSource

    _, caller = plugins
    closed = []

    async def check(**kwargs):
        async def close():
            closed.append(registry.get_current_plugin())

        kwargs["stack"].push_async_callback(close)
        raise asyncio.CancelledError

    monkeypatch.setattr(execution, "_check_matcher", check)
    matcher = Matcher.new(
        source=MatcherSource(plugin_id=caller.id_, module_name="caller")
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await execution.check_and_run_matcher(matcher, *conversation, {})
        assert closed == [caller] and not caller.calls
    finally:
        matcher.destroy()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "phase", ["checking", "rejected", "handling", "transferred"]
)
async def test_cancelled_matcher_finishes_async_finalizers(
    plugins, conversation, monkeypatch, phase
):
    from opsmesh.dispatch import execution
    from opsmesh.matchers import Matcher, MatcherSource

    provider, caller = plugins
    closed = []

    async def close():
        await anyio.sleep(0)
        assert caller.calls and provider.calls
        closed.append(registry.get_current_plugin())

    async def check(**kwargs):
        kwargs["stack"].push_async_callback(close)
        if phase in ("checking", "rejected"):
            cancellation.cancel()
        if phase == "checking":
            await anyio.sleep_forever()
        return phase != "rejected"

    async def run(**kwargs):
        cancellation.cancel()
        await anyio.sleep_forever()

    monkeypatch.setattr(execution, "_check_matcher", check)
    monkeypatch.setattr(execution, "_run_matcher", run)
    matcher = Matcher.new(
        source=MatcherSource(plugin_id=caller.id_, module_name="caller")
    )
    try:
        with anyio.CancelScope() as cancellation:
            async with AsyncExitStack() as resources:
                await execution.check_and_run_matcher(
                    matcher,
                    *conversation,
                    {},
                    stack=resources if phase == "transferred" else None,
                )
        assert closed == [caller] and not caller.calls and not provider.calls
    finally:
        matcher.destroy()


@pytest.mark.anyio
async def test_repeated_drain_preserves_pending_matcher_finalizer(
    plugins, conversation, monkeypatch
):
    from opsmesh.dispatch import execution
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.plugins.invocation import drain_plugin

    provider, caller = plugins
    checking = asyncio.Event()
    closing = asyncio.Event()
    release = asyncio.Event()
    closed = []

    async def close():
        closing.set()
        await release.wait()
        closed.append(True)

    async def check(**kwargs):
        kwargs["stack"].push_async_callback(close)
        checking.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(execution, "_check_matcher", check)
    matcher = Matcher.new(
        source=MatcherSource(plugin_id=caller.id_, module_name="caller")
    )
    request = asyncio.create_task(
        execution.check_and_run_matcher(matcher, *conversation, {})
    )
    try:
        await checking.wait()
        request.cancel()
        await closing.wait()
        provider.status = caller.status = PluginStatus.STOPPING
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await drain_plugin(caller, 0.02)
            assert caller.calls and provider.calls and not closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert closed == [True] and not caller.calls and not provider.calls
    finally:
        release.set()
        matcher.destroy()


@pytest.mark.anyio
async def test_interrupted_finalizer_keeps_admission_after_external_cancel(
    plugins, conversation, monkeypatch
):
    from opsmesh.dispatch import execution
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.plugins.invocation import drain_plugin

    provider, caller = plugins
    checking = asyncio.Event()
    closing = asyncio.Event()

    async def close():
        closing.set()
        await anyio.sleep_forever()

    async def check(**kwargs):
        kwargs["stack"].push_async_callback(close)
        checking.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(execution, "_check_matcher", check)
    matcher = Matcher.new(
        source=MatcherSource(plugin_id=caller.id_, module_name="caller")
    )
    request = asyncio.create_task(
        execution.check_and_run_matcher(matcher, *conversation, {})
    )
    try:
        await checking.wait()
        request.cancel()
        await closing.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        with pytest.raises(TimeoutError):
            await drain_plugin(caller, 0.02)
        assert caller.calls and provider.calls
        assert all(call.active for call in caller.calls.values())
    finally:
        matcher.destroy()


@pytest.mark.anyio
async def test_drain_cancellation_propagates_to_plugin_call(plugins):
    from opsmesh.plugins.invocation import drain_plugin

    _, caller = plugins
    entered = asyncio.Event()

    async def request():
        async with plugin_call(caller.id_):
            entered.set()
            await anyio.sleep_forever()
        pytest.fail("Plugin invocation swallowed cancellation")

    task = asyncio.create_task(request())
    await entered.wait()
    await drain_plugin(caller, 0.02)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not caller.calls


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_method", ["scope", "task"])
async def test_hook_finalizer_releases_admission_only_after_cleanup(
    plugins, cancel_method
):
    from opsmesh.dispatch.hooks import EVENT_PCS_PARAMS
    from opsmesh.params import Depends
    from opsmesh.plugins.invocation import drain_plugin

    provider, caller = plugins
    closing = asyncio.Event()
    release = asyncio.Event()
    closed = []

    async def resource():
        try:
            yield True
        finally:
            closing.set()
            await release.wait()
            await anyio.sleep(0)
            closed.append(True)

    async def observe(value: Annotated[bool, Depends(resource)]):
        pass

    hooks = set()
    with registry.plugin_scope(caller):
        registry.register_hook(hooks, observe, EVENT_PCS_PARAMS)
    resources = AsyncExitStack()
    await next(iter(hooks))(stack=resources)
    if cancel_method == "scope":
        release.set()
        with anyio.CancelScope() as cancellation:
            cancellation.cancel()
            await resources.aclose()
        assert closed == [True] and not caller.calls and not provider.calls
    else:
        cleanup = asyncio.create_task(resources.aclose())
        await closing.wait()
        cleanup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        with pytest.raises(TimeoutError):
            await drain_plugin(caller, 0.02)
        assert not closed and caller.calls and provider.calls


@pytest.mark.anyio
async def test_transferred_continuation_has_an_independent_lifetime(plugins):
    provider, caller = plugins
    resources = AsyncExitStack()
    async with plugin_call(caller.id_) as original:
        provider.status = caller.status = PluginStatus.STOPPING
        continuation = await asyncio.create_task(
            _hold_continuation(original, provider.id_, resources)
        )
        assert continuation is not original
    assert not original.active and continuation.active and provider.calls
    await asyncio.create_task(resources.aclose())
    assert not continuation.active and not provider.calls


async def _hold_continuation(origin, plugin_id, stack):
    return hold_continuation(origin, plugin_id, stack)
