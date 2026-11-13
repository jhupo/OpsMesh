from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated

import anyio
import pytest

from opsmesh import dispatch
from opsmesh.dependencies.context import Context
from opsmesh.dispatch import hooks
from opsmesh.exceptions import IgnoredException, NoLogException
from opsmesh.matchers import Matcher, matchers
from opsmesh.params import Depends


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stage",
    [
        "permission",
        "rule",
        "event_pre",
        "event_post",
        "run_pre",
        "run_post",
        "handler",
    ],
)
async def test_dispatch_result_reports_caught_failures(incoming, stage):
    from opsmesh.permissions import Permission
    from opsmesh.rules import Rule
    from opsmesh.utils import flatten_exception_group

    async def failing():
        raise ValueError(stage)

    async def handled():
        return None

    options = {"handlers": [handled]}
    if stage == "permission":
        options["permission"] = Permission(failing)
    elif stage == "rule":
        options["rule"] = Rule(failing)
    elif stage == "handler":
        options["handlers"] = [failing]
    else:
        register = {
            "event_pre": dispatch.event_preprocessor,
            "event_post": dispatch.event_postprocessor,
            "run_pre": dispatch.run_preprocessor,
            "run_post": dispatch.run_postprocessor,
        }[stage]
        register(failing)
    Matcher.new(**options)
    result = await dispatch.dispatch_event(*incoming)
    with pytest.raises(ExceptionGroup) as caught:
        result.raise_for_errors()
    assert [str(error) for error in flatten_exception_group(caught.value)] == [
        stage
    ]
    with pytest.raises(ExceptionGroup):
        await dispatch.handle_event(*incoming)


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["unmatched", "denied", "ignored"])
async def test_expected_nonexecution_is_not_a_dispatch_failure(
    incoming, outcome
):
    from opsmesh.permissions import Permission

    async def denied():
        return False

    async def ignored():
        raise IgnoredException("Expected policy decision")

    if outcome == "denied":
        Matcher.new(permission=Permission(denied))
    elif outcome == "ignored":
        dispatch.event_preprocessor(ignored)
    result = await dispatch.dispatch_event(*incoming)
    result.raise_for_errors()
    assert result.errors == ()


@pytest.mark.anyio
async def test_concurrent_dispatch_results_do_not_share_failures(incoming):
    channel, _ = incoming
    results = {}
    entered = anyio.Event()

    async def handler(event):
        if event.name == "failed":
            entered.set()
            await anyio.sleep(0)
            raise ValueError("Failed event")
        await entered.wait()

    async def run(name):
        event = SimpleNamespace(
            name=name,
            get_type=lambda: "notice",
            get_log_string=lambda: name,
        )
        results[name] = await dispatch.dispatch_event(channel, event)

    Matcher.new(handlers=[handler])
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run, "failed")
        tasks.start_soon(run, "succeeded")
    assert results["succeeded"].errors == ()
    assert len(results["failed"].errors) == 1


@pytest.mark.anyio
async def test_dispatch_result_is_returned_after_dependency_cleanup(incoming):
    events = []

    async def resource():
        try:
            yield "resource"
        finally:
            events.append("closed")

    dependency = Depends(resource)

    async def handler(value=dependency):
        raise ValueError(value)

    Matcher.new(handlers=[handler])
    result = await dispatch.dispatch_event(*incoming)
    assert events == ["closed"] and len(result.errors) == 1


@pytest.mark.anyio
async def test_context_reaches_rules_permissions_hooks_and_nested_resources(
    conversation,
):
    from opsmesh.permissions import Permission, User
    from opsmesh.rules import Rule

    seen = []
    closed = []

    async def resource(context: Annotated[dict, Context()]):
        try:
            yield context
        finally:
            closed.append(context)

    dependency = Depends(resource)

    async def observe(value=dependency):
        seen.append(value)
        return True

    channel, event = conversation
    permission = Permission(
        User((event.get_session_id(),), Permission(observe))
    )
    Matcher.new(permission=permission, rule=Rule(observe), handlers=[observe])
    for register in (
        dispatch.event_preprocessor,
        dispatch.event_postprocessor,
        dispatch.run_preprocessor,
        dispatch.run_postprocessor,
    ):
        register(observe)
    context = {"user": "alice"}
    result = await dispatch.dispatch_event(
        channel, event, invocation_context=context
    )
    result.raise_for_errors()
    assert len(seen) == 7 and all(value is context for value in seen)
    assert closed == [context, context]
    assert (result.matched, result.handled, result.suspended) == (1, 1, 0)


@pytest.mark.anyio
async def test_interaction_resume_receives_fresh_context(conversation):
    channel, event = conversation
    seen = []
    interaction = Matcher.new(type_="message", temp=True, block=True)

    @interaction.receive()
    async def response(context: Annotated[dict, Context()]):
        seen.append(context)

    first = await dispatch.dispatch_event(
        channel, event, invocation_context={"role": "old"}
    )
    second = await dispatch.dispatch_event(
        channel, event, invocation_context={"role": "current"}
    )
    first.raise_for_errors()
    second.raise_for_errors()
    assert (first.handled, first.suspended) == (0, 1)
    assert (second.handled, second.suspended) == (1, 0)
    assert seen == [{"role": "current"}]


@pytest.mark.anyio
async def test_concurrent_and_nested_contexts_do_not_leak(incoming):
    seen = []

    async def handler(event, context: Annotated[dict, Context()]):
        await anyio.sleep(0)
        seen.append((event.name, context["user"]))
        if event.name == "outer":
            nested = SimpleNamespace(
                get_type=lambda: "notice",
                get_log_string=lambda: "nested",
                name="nested",
            )
            result = await dispatch.dispatch_event(
                incoming[0], nested, invocation_context={"user": "nested"}
            )
            result.raise_for_errors()
            assert result.handled == 1

    Matcher.new(handlers=[handler])

    async def invoke(name):
        event = SimpleNamespace(
            get_type=lambda: "notice", get_log_string=lambda: name, name=name
        )
        result = await dispatch.dispatch_event(
            incoming[0], event, invocation_context={"user": name}
        )
        result.raise_for_errors()
        assert result.handled == 1

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(invoke, "outer")
        tasks.start_soon(invoke, "parallel")
    assert sorted(seen) == [
        ("nested", "nested"),
        ("outer", "outer"),
        ("parallel", "parallel"),
    ]


@pytest.mark.anyio
async def test_context_resource_releases_when_dispatch_is_cancelled(incoming):
    entered = anyio.Event()
    closed = []
    context = {"user": "cancelled"}

    async def resource(value: Annotated[dict, Context()]):
        try:
            yield value
        finally:
            await anyio.sleep(0)
            closed.append(value)

    dependency = Depends(resource)

    async def handler(value=dependency):
        entered.set()
        await anyio.sleep_forever()

    Matcher.new(handlers=[handler])

    async def invoke():
        await dispatch.handle_event(*incoming, invocation_context=context)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(invoke)
            await entered.wait()
            tasks.cancel_scope.cancel()
    assert closed == [context]


@pytest.mark.anyio
async def test_stop_propagation_does_not_cancel_same_priority_peer(incoming):
    calls = []

    async def blocker():
        calls.append("blocked")

    async def peer():
        await anyio.sleep(0.01)
        calls.append("completed")

    async def lower():
        calls.append("lower")

    Matcher.new(priority=1, block=True, handlers=[blocker])
    Matcher.new(priority=1, handlers=[peer])
    Matcher.new(priority=2, handlers=[lower])
    result = await dispatch.dispatch_event(*incoming)
    result.raise_for_errors()
    assert calls == ["blocked", "completed"]
    assert result.handled == 2


@pytest.fixture(autouse=True)
def isolated_dispatch():
    registry = dict(matchers.items())
    matchers.clear()
    processors = (
        hooks._event_preprocessors,
        hooks._event_postprocessors,
        hooks._run_preprocessors,
        hooks._run_postprocessors,
    )
    saved = [set(group) for group in processors]
    for group in processors:
        group.clear()
    yield
    matchers.clear()
    matchers.update(registry)
    for group, previous in zip(processors, saved, strict=True):
        group.clear()
        group.update(previous)


@pytest.fixture
def incoming():
    channel = SimpleNamespace(type="test", self_id="channel")
    event = SimpleNamespace(
        get_type=lambda: "notice",
        get_event_name=lambda: "test.notice",
        get_log_string=lambda: "test event",
    )
    return channel, event


@pytest.mark.anyio
async def test_dispatch_orders_processors_and_releases_shared_dependency(
    incoming,
):
    calls = []

    async def resource():
        calls.append("enter")
        try:
            yield "resource"
        finally:
            calls.append("exit")

    dependency = Depends(resource)

    @dispatch.event_preprocessor
    async def before_event(value=dependency):
        calls.append("event before")

    @dispatch.run_preprocessor
    async def before_matcher(value=dependency):
        calls.append("matcher before")

    async def handler(value=dependency):
        calls.append("handler")

    @dispatch.run_postprocessor
    async def after_matcher():
        calls.append("matcher after")

    @dispatch.event_postprocessor
    async def after_event(value=dependency):
        calls.append("event after")

    Matcher.new(handlers=[handler])
    await dispatch.handle_event(*incoming)
    assert calls == [
        "enter",
        "event before",
        "enter",
        "matcher before",
        "handler",
        "matcher after",
        "event after",
        "exit",
        "exit",
    ]


@pytest.mark.anyio
async def test_blocking_matcher_preserves_peers_and_stops_lower_priorities(
    incoming,
):
    calls = []
    peer_started = anyio.Event()

    async def blocking():
        await peer_started.wait()
        calls.append("blocking")

    async def peer():
        calls.append("peer")
        peer_started.set()

    async def lower():
        calls.append("lower")

    Matcher.new(priority=1, block=True, handlers=[blocking])
    Matcher.new(priority=1, handlers=[peer])
    Matcher.new(priority=2, handlers=[lower])
    with anyio.fail_after(2):
        await dispatch.handle_event(*incoming)
    assert calls == ["peer", "blocking"]


@pytest.mark.anyio
async def test_ignored_event_stops_dispatch_and_releases_resources(incoming):
    calls = []

    async def resource():
        try:
            yield
        finally:
            calls.append("cleanup")

    dependency = Depends(resource)

    @dispatch.event_preprocessor
    async def ignore(value=dependency):
        raise IgnoredException("Ignored by test")

    async def handler():
        calls.append("handler")

    Matcher.new(handlers=[handler])
    await dispatch.handle_event(*incoming)
    assert calls == ["cleanup"]


@pytest.mark.anyio
async def test_handler_failure_reaches_postprocessors_and_next_matcher(
    incoming,
):
    calls = []

    async def failing():
        raise ValueError("Handler failed")

    @dispatch.run_postprocessor
    async def after(exception):
        calls.append(type(exception).__name__)

    async def next_handler():
        calls.append("next")

    Matcher.new(priority=1, handlers=[failing])
    Matcher.new(priority=2, handlers=[next_handler])
    with pytest.raises(ExceptionGroup, match="Event dispatch failed"):
        await dispatch.handle_event(*incoming)
    assert calls == ["ValueError", "next", "NoneType"]


@pytest.mark.anyio
async def test_temporary_and_expired_matchers_are_removed(incoming):
    calls = []

    async def temporary():
        calls.append("temporary")

    async def expired():
        calls.append("expired")

    Matcher.new(temp=True, handlers=[temporary])
    Matcher.new(
        expire_time=datetime.now(UTC) - timedelta(seconds=1),
        handlers=[expired],
    )
    await dispatch.handle_event(*incoming)
    await dispatch.handle_event(*incoming)
    assert (calls, list(matchers.get(1, []))) == (["temporary"], [])


@pytest.mark.anyio
async def test_no_log_event_still_reaches_handlers(incoming, caplog):
    channel, event = incoming
    calls = []

    def hidden_log():
        raise NoLogException("test")

    event.get_log_string = hidden_log

    async def handler():
        calls.append("handler")

    Matcher.new(handlers=[handler])
    with caplog.at_level("INFO"):
        await dispatch.handle_event(channel, event)
    assert calls == ["handler"] and "test event" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("control", ["pause", "reject"])
async def test_continuation_keeps_permission_resources_and_event_cache(
    control, conversation
):
    from opsmesh.permissions import Permission

    channel, event = conversation
    lifecycle = []
    calls = []

    async def resource():
        lifecycle.append("enter")
        try:
            yield object()
        finally:
            lifecycle.append("exit")

    dependency = Depends(resource)

    async def allowed(value=dependency):
        calls.append(("permission", value))
        return True

    async def first(matcher: Matcher, value=dependency):
        calls.append(("first", value))
        if sum(name == "first" for name, _ in calls) == 1:
            await getattr(matcher, control)()

    async def second(value=dependency):
        calls.append(("second", value))

    Matcher.new(
        temp=True, permission=Permission(allowed), handlers=[first, second]
    )
    await dispatch.handle_event(channel, event)
    await dispatch.handle_event(channel, event)
    names = [name for name, _ in calls]
    assert names == (
        ["permission", "first", "permission", "second"]
        if control == "pause"
        else ["permission", "first", "permission", "first", "second"]
    )
    assert lifecycle == ["enter", "exit", "enter", "exit"]
    assert calls[0][1] is calls[1][1] and calls[2][1] is calls[-1][1]
    assert calls[0][1] is not calls[2][1]
