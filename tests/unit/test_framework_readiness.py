"""Driver readiness, Gateway hooks, Permissions, and Matcher contracts."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import anyio
import anyio.lowlevel
from pydantic import TypeAdapter
import pytest

import opsmesh
from opsmesh.adapters import Adapter, Event, Gateway, Message, MessageSegment
from opsmesh.config import Config, Env
from opsmesh.drivers import Request, Timeout
from opsmesh.drivers.fastapi import Driver as FastAPIDriver
from opsmesh.drivers.none import Driver as NoneDriver
from opsmesh.exception import ActionNotAvailable, MockInvokeResult
from opsmesh.matcher import Matcher
from opsmesh.message import handle_event
from opsmesh.params import Depends, EventTargeted
from opsmesh.permission import SUPERUSER
from opsmesh.plugin import MatcherGroup, on_command, on_message
from opsmesh.rule import TrieRule, targeted


class TextSeg(MessageSegment["TextMsg"]):
    @classmethod
    def get_message_class(cls):
        return TextMsg

    def __str__(self) -> str:
        return self.data["text"]

    def is_text(self) -> bool:
        return True


class TextMsg(Message[TextSeg]):
    @classmethod
    def get_segment_class(cls):
        return TextSeg

    @staticmethod
    def _construct(msg: str):
        yield TextSeg("text", {"text": msg})


class ProbeAdapter(Adapter):
    @classmethod
    def get_name(cls) -> str:
        return "probe"

    async def _invoke(self, gateway: Gateway, action: str, **params):
        if action == "ping":
            return {"gateway": gateway.id, **params}
        raise ActionNotAvailable(self.get_name(), action)


class ProbeGateway(Gateway):
    def __init__(self, adapter: Adapter, id: str):
        super().__init__(adapter, id)
        self.sent: list[str] = []

    async def send(self, event, message, **kwargs):
        self.sent.append(str(message))
        return str(message)


class ProbeEvent(Event):
    kind: str = "notice"
    payload: str = "probe"
    user_id: str = "user_1"
    targeted_flag: bool = False

    def get_type(self) -> str:
        return self.kind

    def get_event_name(self) -> str:
        return f"probe.{self.kind}"

    def get_event_description(self) -> str:
        return self.payload

    def get_user_id(self) -> str:
        return self.user_id

    def get_session_id(self) -> str:
        return f"session_{self.user_id}"

    def get_message(self) -> TextMsg:
        return TextMsg(self.payload)

    def is_targeted(self) -> bool:
        return self.targeted_flag


def create_driver(**config_kwargs) -> NoneDriver:
    return NoneDriver(Env(), Config(**config_kwargs))


def create_gateway(driver: NoneDriver, gw_id: str = "probe:1") -> ProbeGateway:
    return ProbeGateway(ProbeAdapter(driver), gw_id)


async def wait_until(condition, max_wait=1.0):
    deadline = anyio.current_time() + max_wait
    while not condition():
        if anyio.current_time() > deadline:
            raise AssertionError("condition was not reached in time")
        await anyio.sleep(0.001)


@pytest.mark.anyio
async def test_wait_started_completes_after_ready_funcs():
    driver = create_driver()
    order = []
    finished = anyio.Event()

    async def observe():
        await driver.wait_started()
        order.append("waiter")
        finished.set()

    @driver.on_startup
    async def start():
        driver.task_group.start_soon(observe)

    @driver.on_ready
    async def ready():
        order.append("ready")

    async with driver._lifespan:
        with anyio.fail_after(1):
            await finished.wait()
    assert order == ["ready", "waiter"]


@pytest.mark.anyio
async def test_wait_started_propagates_ready_failure():
    driver = create_driver()
    lifespan = driver._lifespan
    errors = []
    finished = anyio.Event()

    async def observe():
        try:
            await driver.wait_started()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    @driver.on_startup
    async def start():
        driver.task_group.start_soon(observe)

    @driver.on_ready
    async def fail():
        raise RuntimeError("ready failed")

    with pytest.raises(RuntimeError, match="ready failed"):
        await lifespan.startup()
    with anyio.fail_after(1):
        await finished.wait()
    await lifespan.shutdown()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


@pytest.mark.anyio
async def test_fastapi_lifespan_cleans_up_on_ready_failure():
    driver = FastAPIDriver(Env(), Config())
    shutdown_called = False
    bg_cancelled = False

    async def bg_worker():
        nonlocal bg_cancelled
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            bg_cancelled = True
            raise

    @driver.on_startup
    async def start():
        driver.task_group.start_soon(bg_worker)
        await anyio.lowlevel.checkpoint()

    @driver.on_ready
    async def fail():
        raise RuntimeError("ready failed")

    @driver.on_shutdown
    async def on_stop():
        nonlocal shutdown_called
        shutdown_called = True

    with pytest.raises(RuntimeError, match="ready failed"):
        async with driver._lifespan_manager(driver.server_app):
            pass

    assert shutdown_called is True
    assert bg_cancelled is True
    assert driver._lifespan._task_group is None


def test_driver_adapters_are_isolated_per_instance(monkeypatch):
    d1 = create_driver()
    d2 = create_driver()
    a1 = d1.register_adapter(ProbeAdapter)
    monkeypatch.setattr(opsmesh, "_driver", d2)
    a2 = opsmesh.register_adapter(ProbeAdapter)

    assert a1 is not a2
    assert a1.driver is d1
    assert a2.driver is d2
    assert opsmesh.register_adapter(ProbeAdapter) is a2

    monkeypatch.setattr(opsmesh, "_driver", None)
    with pytest.raises(ValueError, match="OpsMesh has not been initialized"):
        opsmesh.run()

    called_with: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        d1, "run", lambda *args, **kwargs: called_with.append((args, kwargs))
    )
    monkeypatch.setattr(opsmesh, "_driver", d1)
    opsmesh.run(host="127.0.0.1", port=9000)
    assert called_with == [((), {"host": "127.0.0.1", "port": 9000})]


def test_request_and_matcher_timedelta_utc_models():
    req = Request(
        "GET",
        "https://example.com",
        timeout=Timeout(total=10.0, ping=5.0),
        ping_interval=15.0,
        auto_redirects=False,
    )
    assert isinstance(req.timeout, Timeout)
    assert req.timeout.ping == 5.0
    assert req.ping_interval == 15.0
    assert req.auto_redirects is False

    m = Matcher.new("notice", expire_time=timedelta(seconds=30))
    try:
        assert m.expire_time is not None
        assert m.expire_time.tzinfo == UTC
    finally:
        m.destroy()

    msg_adapter = TypeAdapter(TextMsg)
    parsed = msg_adapter.validate_python([{"type": "text", "data": {"text": "ok"}}])
    assert isinstance(parsed, TextMsg)
    assert str(parsed) == "ok"

    untyped_m = Matcher.new("notice")
    try:
        with pytest.raises(ValueError, match="Unknown parameter event"):

            @untyped_m.handle()
            async def _(event):
                pass
    finally:
        untyped_m.destroy()


@pytest.mark.anyio
async def test_gateway_connect_disconnect_dep_order_and_invoke_hooks():
    driver = create_driver()
    adapter = ProbeAdapter(driver)
    gw = ProbeGateway(adapter, "gw:shortcut")
    events: list[str] = []

    before_connect = set(Gateway._connect_hooks)
    before_disconnect = set(Gateway._disconnect_hooks)
    before_inv = set(Gateway._before_invoke_hooks)
    after_inv = set(Gateway._after_invoke_hooks)

    async def disconnect_resource() -> AsyncGenerator[str, None]:
        events.append("dep:enter")
        yield "res"
        events.append("dep:exit")

    @Gateway.on_connect
    async def on_conn(gateway: Gateway):
        events.append(f"connect:{gateway.id}")

    @Gateway.on_disconnect
    async def on_disc(gateway: Gateway, res: str = Depends(disconnect_resource)):
        await anyio.sleep(0.01)
        events.append(f"disconnect:{gateway.id}:{res}")

    @Gateway.before_invoke
    async def intercept(gateway: Gateway, action: str, params: dict):
        if action == "mocked":
            raise MockInvokeResult({"mocked": params["v"]})

    @Gateway.after_invoke
    async def post(gateway: Gateway, exc, action: str, params: dict, result):
        events.append(f"after:{action}:{exc is None}")

    try:
        async with driver._lifespan:
            gw.connect()
            assert driver.gateways["gw:shortcut"] is gw
            assert adapter.gateways["gw:shortcut"] is gw
            await wait_until(lambda: "connect:gw:shortcut" in events)

            res = await gw.ping(seq=1)
            assert res == {"gateway": "gw:shortcut", "seq": 1}

            mocked = await gw.invoke("mocked", v=42)
            assert mocked == {"mocked": 42}

            with pytest.raises(ActionNotAvailable):
                await gw.unknown_action()

            gw.disconnect()
            assert "gw:shortcut" not in driver.gateways
            await wait_until(lambda: "dep:exit" in events)
    finally:
        Gateway._connect_hooks.clear()
        Gateway._connect_hooks.update(before_connect)
        Gateway._disconnect_hooks.clear()
        Gateway._disconnect_hooks.update(before_disconnect)
        Gateway._before_invoke_hooks.clear()
        Gateway._before_invoke_hooks.update(before_inv)
        Gateway._after_invoke_hooks.clear()
        Gateway._after_invoke_hooks.update(after_inv)

    assert "after:ping:True" in events
    assert "after:mocked:True" in events
    assert "after:unknown_action:False" in events
    # Generator dependency must exit AFTER disconnect hook body finishes
    assert events.index("dep:enter") < events.index("disconnect:gw:shortcut:res")
    assert events.index("disconnect:gw:shortcut:res") < events.index("dep:exit")


@pytest.mark.anyio
async def test_superuser_permission_matcher_group_and_command_default_block(
    monkeypatch,
):
    driver = create_driver(superusers={"probe:ops_root"})
    monkeypatch.setattr(opsmesh, "_driver", driver)
    gw = create_gateway(driver)
    hits: list[str] = []

    group = MatcherGroup(permission=SUPERUSER)
    superuser_notice = group.on_notice()

    @superuser_notice.handle()
    async def _(event: Event):
        hits.append(f"notice:{event.get_user_id()}")

    cmd_matcher = on_command("deploy", priority=1)
    fallback_msg = on_message(priority=10)
    assert cmd_matcher.block is True
    assert fallback_msg.block is True
    assert superuser_notice.block is False

    @cmd_matcher.handle()
    async def _():
        hits.append("cmd:deploy")

    @fallback_msg.handle()
    async def _():
        hits.append("msg:fallback")

    try:
        # Unauthorized or unscoped user blocked; only adapter-scoped superuser passes
        await handle_event(gw, ProbeEvent(kind="notice", user_id="guest"))
        await handle_event(create_gateway(driver, "other:1"), ProbeEvent())
        assert hits == []

        await handle_event(gw, ProbeEvent(kind="notice", user_id="ops_root"))
        assert hits == ["notice:ops_root"]

        # on_command defaults to block=True, preventing fallthrough to fallback_msg
        hits.clear()
        await handle_event(gw, ProbeEvent(kind="message", payload="/deploy"))
        assert hits == ["cmd:deploy"]

        await handle_event(gw, ProbeEvent(kind="message", payload="hello"))
        assert hits == ["cmd:deploy", "msg:fallback"]
    finally:
        superuser_notice.destroy()
        cmd_matcher.destroy()
        fallback_msg.destroy()
        TrieRule.prefix.pop("/deploy", None)


@pytest.mark.anyio
async def test_event_time_targeted_and_non_message_pause_continuation():
    driver = create_driver()
    gw = create_gateway(driver)
    seen: list[tuple[str, bool, datetime]] = []

    flow = Matcher.new("webhook", rule=targeted(), block=True)

    @flow.handle()
    async def step_1(event: Event, is_target: bool = EventTargeted()):
        seen.append(("step1", is_target, event.get_time()))
        await flow.pause("waiting-callback")

    @flow.handle()
    async def step_2(event: Event):
        seen.append(("step2", event.is_targeted(), event.get_time()))
        await flow.finish("done")

    try:
        ev_ignored = ProbeEvent(kind="webhook", payload="init", targeted_flag=False)
        await handle_event(gw, ev_ignored)
        assert seen == []

        ev_init = ProbeEvent(kind="webhook", payload="init", targeted_flag=True)
        await handle_event(gw, ev_init)
        assert len(seen) == 1
        assert seen[0][0] == "step1"
        assert gw.sent == ["waiting-callback"]

        # Non-message ("webhook") resumes automatically on next "webhook" event
        ev_cb = ProbeEvent(kind="webhook", payload="cb", targeted_flag=False)
        await handle_event(gw, ev_cb)
        assert len(seen) == 2
        assert seen[1][0] == "step2"
        assert gw.sent == ["waiting-callback", "done"]
    finally:
        flow.destroy()
