"""Verify continuation identity, access checks, and resource cleanup."""

import asyncio
from datetime import datetime, timedelta
from types import ModuleType

import pytest

from opsmesh.dispatch import dispatch_event
from opsmesh.lifecycle import Lifespan
from opsmesh.matchers import Matcher, MatcherSource, matchers
from opsmesh.permissions import Permission
from opsmesh.plugins import registry
from opsmesh.plugins.model import Plugin, PluginStatus


@pytest.fixture(autouse=True)
def isolated_matchers():
    saved = dict(matchers.items())
    matchers.clear()
    yield
    matchers.clear()
    matchers.update(saved)


@pytest.fixture
def plugin(monkeypatch):
    module = ModuleType("continuation_test")
    plugin = Plugin(module.__name__, module, module.__name__, None)
    monkeypatch.setattr(registry, "_plugins", {plugin.id_: plugin})
    return plugin


@pytest.mark.parametrize("temporary", [False, True])
def test_declared_matcher_is_its_own_origin(temporary):
    matcher = Matcher.new(temp=temporary)

    assert matcher.origin is matcher


@pytest.mark.anyio
@pytest.mark.parametrize("control", ["pause", "reject"])
async def test_repeated_continuations_retain_the_root_matcher(
    conversation, control
):
    calls = []
    root = Matcher.new(type_="message", block=True)
    unrelated = Matcher.new(type_="message", temp=True)

    async def first(matcher: Matcher):
        calls.append("first")
        if control == "pause":
            await matcher.pause()
        count = matcher.state.get("attempt", 0) + 1
        matcher.state["attempt"] = count
        if count < 3:
            await matcher.reject()

    async def second(matcher: Matcher):
        calls.append("second")
        if control == "pause":
            await matcher.pause()

    async def final():
        calls.append("final")

    root.append_handler(first)
    root.append_handler(second)
    root.append_handler(final)
    for _ in range(2):
        outcome = await dispatch_event(
            *conversation,
            matcher_filter=lambda item: item.origin is root,
        )
        outcome.raise_for_errors()
        assert outcome.suspended == 1
        continuation = matchers[0][0]
        assert continuation.origin is root
        assert unrelated in matchers[1]

    outcome = await dispatch_event(
        *conversation,
        matcher_filter=lambda item: item.origin is root,
    )
    outcome.raise_for_errors()
    assert outcome.suspended == 0
    assert calls[-1] == "final"
    assert not matchers[0]


@pytest.mark.anyio
async def test_shared_source_does_not_grant_an_unbound_matcher_access(
    conversation,
):
    calls = []
    source = MatcherSource(module_name=__name__, lineno=1)
    bound = Matcher.new(type_="message", source=source)
    unbound = Matcher.new(type_="message", source=source)

    @unbound.handle()
    async def prompt(matcher: Matcher):
        await matcher.pause()

    @unbound.handle()
    async def receive():
        calls.append("received")

    outcome = await dispatch_event(
        *conversation, matcher_filter=lambda item: item is unbound
    )
    outcome.raise_for_errors()

    outcome = await dispatch_event(
        *conversation, matcher_filter=lambda item: item.origin is bound
    )
    outcome.raise_for_errors()

    assert calls == []
    assert matchers[0][0].origin is unbound


@pytest.mark.anyio
@pytest.mark.parametrize("restriction", ["conversation", "permission"])
async def test_continuation_rechecks_scope_and_permission(
    conversation, monkeypatch, restriction
):
    calls = []
    allowed = True

    async def permitted():
        return allowed

    root = Matcher.new(
        type_="message", permission=Permission(permitted), block=True
    )

    @root.handle()
    async def prompt(matcher: Matcher):
        await matcher.pause()

    @root.handle()
    async def receive():
        calls.append("received")

    outcome = await dispatch_event(*conversation)
    outcome.raise_for_errors()
    continuation = matchers[0][0]
    if restriction == "conversation":
        monkeypatch.setattr(
            conversation[1], "get_session_id", lambda: "other-conversation"
        )
    else:
        allowed = False

    outcome = await dispatch_event(
        *conversation, matcher_filter=lambda item: item is continuation
    )
    outcome.raise_for_errors()

    assert calls == []
    assert continuation in matchers[0]


@pytest.mark.anyio
async def test_expired_continuation_is_removed_before_invocation(conversation):
    calls = []
    root = Matcher.new(type_="message", block=True)

    @root.handle()
    async def prompt(matcher: Matcher):
        await matcher.pause()

    @root.handle()
    async def receive():
        calls.append("received")

    outcome = await dispatch_event(*conversation)
    outcome.raise_for_errors()
    continuation = matchers[0][0]
    continuation.expire_time = datetime.now() - timedelta(seconds=1)

    outcome = await dispatch_event(
        *conversation, matcher_filter=lambda item: item is continuation
    )
    outcome.raise_for_errors()
    assert calls == []
    assert continuation not in matchers[0]


@pytest.mark.anyio
@pytest.mark.parametrize("wait_phase", ["ready", "draining"])
async def test_resource_restart_removes_waits_and_preserves_static_matcher(
    conversation, plugin, wait_phase
):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    lifespan = Lifespan()
    with registry.plugin_scope(plugin):
        root = Matcher.new(
            type_="message",
            block=True,
            source=MatcherSource(plugin_id=plugin.id_),
        )
        if wait_phase == "draining":
            lifespan.on_stopping(release.set)

    @root.handle()
    async def prompt(matcher: Matcher):
        calls.append("prompt")
        entered.set()
        if wait_phase == "draining":
            await release.wait()
            assert plugin.status is PluginStatus.STOPPING
        await matcher.pause()

    @root.handle()
    async def receive():
        calls.append("received")

    async with lifespan:
        pending = asyncio.create_task(dispatch_event(*conversation))
        await entered.wait()
        if wait_phase == "ready":
            outcome = await pending
            outcome.raise_for_errors()
            assert matchers[0]

        await lifespan.stop_plugin(plugin)
        outcome = await pending
        outcome.raise_for_errors()
        assert outcome.suspended == 1
        assert not matchers[0]
        assert root in matchers[root.priority]
        assert not plugin.activation_cleanups

        await lifespan.start_plugin(plugin)
        outcome = await dispatch_event(
            *conversation, matcher_filter=lambda item: item.priority == 0
        )
        outcome.raise_for_errors()
        assert outcome.matched == 0
        assert calls == ["prompt"]


@pytest.mark.anyio
async def test_revocation_removes_static_matcher_after_resource_stop(
    conversation, plugin
):
    lifespan = Lifespan()
    with registry.plugin_scope(plugin):
        root = Matcher.new(
            type_="message", source=MatcherSource(plugin_id=plugin.id_)
        )

    @root.handle()
    async def prompt(matcher: Matcher):
        await matcher.pause()

    async with lifespan:
        outcome = await dispatch_event(*conversation)
        outcome.raise_for_errors()
        await lifespan.stop_plugin(plugin)
        registry.revoke_plugin(plugin)

        assert root not in matchers[root.priority]
        assert not matchers[0]


@pytest.mark.anyio
async def test_host_shutdown_removes_every_wait_and_keeps_static_matchers(
    conversation, plugin
):
    lifespan = Lifespan()

    async def prompt(matcher: Matcher):
        await matcher.pause()

    with registry.plugin_scope(plugin):
        roots = [
            Matcher.new(
                type_="message",
                handlers=[prompt],
                source=MatcherSource(plugin_id=plugin.id_),
            )
            for _ in range(2)
        ]

    async with lifespan:
        outcome = await dispatch_event(*conversation)
        outcome.raise_for_errors()
        assert outcome.suspended == 2
        assert len(matchers[0]) == 2

    assert not matchers[0]
    assert matchers[1] == roots
    assert not plugin.activation_cleanups
