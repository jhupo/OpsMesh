"""Event pipeline hooks, Matcher priorities, got/receive/reject, and single_session."""

from datetime import UTC, datetime, timedelta

import anyio
import pytest

import opsmesh
from opsmesh.adapters import Adapter, Event, Gateway, Message, MessageSegment
from opsmesh.config import Config, Env
from opsmesh.drivers.none import Driver as NoneDriver
from opsmesh.exception import IgnoredException
from opsmesh.matcher import Matcher
from opsmesh.message import (
    _event_postprocessors,
    _event_preprocessors,
    _run_postprocessors,
    _run_preprocessors,
    event_postprocessor,
    event_preprocessor,
    handle_event,
    run_postprocessor,
    run_preprocessor,
)
from opsmesh.params import ArgPlainText, LastReceived, Received


class _Seg(MessageSegment["_Msg"]):
    @classmethod
    def get_message_class(cls):
        return _Msg

    def __str__(self) -> str:
        return self.data["text"]

    def is_text(self) -> bool:
        return True


class _Msg(Message[_Seg]):
    @classmethod
    def get_segment_class(cls):
        return _Seg

    @staticmethod
    def _construct(msg: str):
        yield _Seg("text", {"text": msg})


class _Adapter(Adapter):
    @classmethod
    def get_name(cls) -> str:
        return "dispatch"

    async def _invoke(self, gateway: Gateway, action: str, **params):
        return None


class _Gateway(Gateway):
    def __init__(self, adapter: Adapter, id: str):
        super().__init__(adapter, id)
        self.sent: list[str] = []

    async def send(self, event, message, **kwargs):
        self.sent.append(str(message))
        return str(message)


class _Event(Event):
    kind: str = "message"
    text: str = "hello"
    sid: str = "sess:1"

    def get_type(self) -> str:
        return self.kind

    def get_event_name(self) -> str:
        return f"dispatch.{self.kind}"

    def get_event_description(self) -> str:
        return self.text

    def get_user_id(self) -> str:
        return "u1"

    def get_session_id(self) -> str:
        return self.sid

    def get_message(self) -> _Msg:
        return _Msg(self.text)

    def is_tome(self) -> bool:
        return True


def _make_gw() -> _Gateway:
    driver = NoneDriver(Env(), Config())
    return _Gateway(_Adapter(driver), "dispatch:1")


@pytest.mark.anyio
async def test_event_and_run_processors_plus_exception_injection():
    gw = _make_gw()
    log: list[str] = []

    pre_ev_before = set(_event_preprocessors)
    post_ev_before = set(_event_postprocessors)
    pre_run_before = set(_run_preprocessors)
    post_run_before = set(_run_postprocessors)

    @event_preprocessor
    async def ev_pre(event: Event):
        log.append(f"ev_pre:{event.get_plaintext()}")
        if event.get_plaintext() == "drop":
            raise IgnoredException("dropped")

    @event_postprocessor
    async def ev_post(event: Event):
        log.append(f"ev_post:{event.get_plaintext()}")

    @run_preprocessor
    async def r_pre(event: Event):
        log.append("run_pre")
        if event.get_plaintext() == "cancel":
            raise IgnoredException("cancelled")

    @run_postprocessor
    async def r_post(exc: Exception | None):
        log.append(f"run_post:{type(exc).__name__ if exc else 'ok'}")

    m = Matcher.new("message", block=True)

    @m.handle()
    async def _(event: Event):
        log.append(f"handle:{event.get_plaintext()}")
        if event.get_plaintext() == "boom":
            raise RuntimeError("boom")

    try:
        await handle_event(gw, _Event(text="drop"))
        assert log == ["ev_pre:drop"]

        log.clear()
        await handle_event(gw, _Event(text="cancel"))
        assert log == ["ev_pre:cancel", "run_pre", "ev_post:cancel"]

        log.clear()
        await handle_event(gw, _Event(text="boom"))
        assert log == [
            "ev_pre:boom",
            "run_pre",
            "handle:boom",
            "run_post:RuntimeError",
            "ev_post:boom",
        ]
    finally:
        m.destroy()
        _event_preprocessors.difference_update(
            set(_event_preprocessors) - pre_ev_before
        )
        _event_postprocessors.difference_update(
            set(_event_postprocessors) - post_ev_before
        )
        _run_preprocessors.difference_update(set(_run_preprocessors) - pre_run_before)
        _run_postprocessors.difference_update(
            set(_run_postprocessors) - post_run_before
        )


@pytest.mark.anyio
async def test_priority_block_temp_expire_and_skip():
    gw = _make_gw()
    hits: list[str] = []

    expired = Matcher.new(
        "notice", expire_time=datetime.now(UTC) - timedelta(seconds=1)
    )
    temp_m = Matcher.new("notice", temp=True, priority=1, block=False)
    high = Matcher.new("notice", priority=2, block=False)
    low = Matcher.new("notice", priority=3, block=False)

    @expired.handle()
    async def _():
        hits.append("expired")

    @temp_m.handle()
    async def _():
        hits.append("temp")

    @high.handle()
    async def _():
        hits.append("high_1")
        high.skip()
        hits.append("unreachable")

    @high.handle()
    async def _(matcher: Matcher):
        hits.append("high_2")
        matcher.stop_propagation()

    @low.handle()
    async def _():
        hits.append("low")

    try:
        await handle_event(gw, _Event(kind="notice"))
        assert hits == ["temp", "high_1", "high_2"]

        # temp matcher already destroyed; second event does not hit temp
        hits.clear()
        await handle_event(gw, _Event(kind="notice"))
        assert hits == ["high_1", "high_2"]
    finally:
        for item in (temp_m, high, low):
            try:
                item.destroy()
            except ValueError:
                pass


@pytest.mark.anyio
async def test_got_reject_arg_and_receive_multiturn_flow():
    gw = _make_gw()
    captured: list[str] = []

    flow = Matcher.new("message", block=True)

    @flow.got("target", prompt="ask-target")
    async def step_got(target: str = ArgPlainText()):
        if target != "api":
            await flow.reject_arg("target", "retry-target")
        captured.append(f"got:{target}")

    @flow.receive("confirm")
    async def step_recv(ev: Event = Received("confirm"), last: Event = LastReceived()):
        assert ev is last
        captured.append(f"recv:{ev.get_plaintext()}")
        await flow.finish("all-done")

    try:
        await handle_event(gw, _Event(text="start"))
        assert gw.sent == ["ask-target"]

        await handle_event(gw, _Event(text="bad"))
        assert gw.sent == ["ask-target", "retry-target"]

        await handle_event(gw, _Event(text="api"))
        assert captured == ["got:api"]

        await handle_event(gw, _Event(text="yes"))
        assert captured == ["got:api", "recv:yes"]
        assert gw.sent == ["ask-target", "retry-target", "all-done"]
    finally:
        flow.destroy()


@pytest.mark.anyio
async def test_single_session_builtin_plugin_drops_concurrent_same_session(
    monkeypatch,
):
    driver = NoneDriver(Env(), Config())
    monkeypatch.setattr(opsmesh, "_driver", driver)
    gw = _Gateway(_Adapter(driver), "dispatch:ss")

    from opsmesh import plugin as plugin_module

    pre_before = set(_event_preprocessors)
    plugins_before = dict(plugin_module._plugins)
    managers_before = list(plugin_module._managers)
    plugin = opsmesh.load_builtin_plugin("single_session")
    assert plugin is not None

    entered = anyio.Event()
    release = anyio.Event()
    seen: list[str] = []

    m = Matcher.new("notice")

    @m.handle()
    async def _(event: Event):
        seen.append(event.get_plaintext())
        if event.get_plaintext() == "first":
            entered.set()
            await release.wait()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(handle_event, gw, _Event(kind="notice", text="first"))
            await entered.wait()
            # Same session concurrent event is dropped
            await handle_event(gw, _Event(kind="notice", text="dropped"))
            # Different session event runs normally
            await handle_event(
                gw, _Event(kind="notice", text="other-sess", sid="sess:2")
            )
            release.set()

        # After first completes, session lock is released
        await handle_event(gw, _Event(kind="notice", text="after"))
        assert seen == ["first", "other-sess", "after"]
    finally:
        m.destroy()
        _event_preprocessors.difference_update(set(_event_preprocessors) - pre_before)
        plugin_module._plugins.clear()
        plugin_module._plugins.update(plugins_before)
        plugin_module._managers[:] = managers_before
        import sys

        sys.modules.pop("opsmesh.plugins.single_session", None)
