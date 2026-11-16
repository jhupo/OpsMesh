"""Exercise dependency-owned robots through the real plugin lifecycle."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


BOOTSTRAP = """
import asyncio
import anyio
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver, set_driver
from opsmesh.plugins import load_plugin, plugin_scope, registry
from opsmesh.plugins.manager import PluginManager
from opsmesh.dispatch import dispatch_event

driver = Driver(Env(), Config(command_start={"/"}))
set_driver(driver)
driver._lifespan.shutdown_timeout = 2
channels = load_plugin("opsmesh.plugins.channels")
assert channels is not None

async def receive(stream):
    try:
        with anyio.CancelScope() as scope:
            stream._scope = scope
            if stream._stopping:
                return
            assert stream.channel._owner.ready
            stream.adapter.stream_connected(stream.channel)
            await anyio.sleep_forever()
    finally:
        stream._scope = None
        stream.adapter.stream_disconnected(stream.channel)
        stream.adapter.stream_finished(stream.channel)
        stream._finished.set()

from opsmesh.plugins.channels.qq.stream import QQStream
from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream
QQStream.run = receive
DingTalkStream.run = receive

def connections(protocol, account):
    values = (
        {"app_id": account, "secret_ref": "TEST_ROBOT_SECRET"}
        if protocol == "qq" else
        {"client_id": account, "client_secret_ref": "TEST_ROBOT_SECRET"}
    )
    return {"channels": {protocol: {"main": values}}}

async def online(channel):
    with anyio.fail_after(2):
        while not channel.ready:
            await anyio.sleep(0)

def event(channel, group, content="hello"):
    values = dict(connection_id=channel.self_id, user_id="user", message_id="message",
                  content=content, raw={})
    if channel.adapter.get_name() == "qq":
        from opsmesh.plugins.channels.qq.events import QQMessageEvent
        return QQMessageEvent(**values, event_id="event", group_id=group)
    from opsmesh.plugins.channels.dingtalk.events import DingTalkMessageEvent
    return DingTalkMessageEvent(**values, conversation_id=group or "private",
                               conversation_type="2" if group else "1")
"""

CONSUMER = """
from opsmesh import on_message, on_command, require
channels = require("channels")
handled = []
sales = on_message(rule=channels.in_group("group-a", connection="main"))
technical = on_message(rule=channels.in_group("group-b", connection="main"))
clear = on_command("clear", rule=channels.in_group("group-a", connection="main"), block=True)
@sales.handle()
async def sales_answer():
    handled.append("sales")
@technical.handle()
async def technical_answer():
    handled.append("technical")
@clear.handle()
async def clear_history():
    handled.append("clear")
"""


def run_script(tmp_path, script, consumer=CONSUMER):
    (tmp_path / "configured_bot.py").write_text(consumer, encoding="utf-8")
    (tmp_path / "other_bot.py").write_text(consumer, encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP + script],
        cwd=root,
        env={
            **os.environ,
            "TEST_ROBOT_SECRET": "test-secret",
            "PYTHONPATH": os.pathsep.join((str(root / "src"), str(tmp_path))),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
def test_automatic_connections_route_groups_and_restart(tmp_path, protocol):
    run_script(
        tmp_path,
        f"protocol = {protocol!r}\n"
        + """
owner = load_plugin("configured_bot", dependencies=connections(protocol, "123"))
other = load_plugin("other_bot", dependencies=connections(protocol, "456"))
assert owner and other
assert not driver.adapters
async def main():
    async with driver.lifespan():
        adapter = driver.get_adapter(protocol)
        first, second = adapter.declarations
        await online(first)
        await online(second)
        assert first.owner_id == "configured_bot"
        for group in ("group-a", "group-b", "ignored", None):
            result = await dispatch_event(first, event(first, group), matcher_filter=first.accepts_matcher)
            assert not result.errors, result.errors
        assert owner.module.handled == ["sales", "technical"]
        assert not other.module.handled
        result = await dispatch_event(first, event(first, "group-a", "/clear"), matcher_filter=first.accepts_matcher)
        assert not result.errors, result.errors
        assert owner.module.handled[-1] == "clear"
        await driver.stop_plugin(owner)
        assert adapter.declarations == (second,)
        assert second.ready and adapter.started
        owner.dependency_config_values = connections(protocol, "789")
        await driver.start_plugin(owner)
        replacement = next(c for c in adapter.declarations if c.owner_id == owner.id_)
        await online(replacement)
        assert replacement is not first
        assert first.state == "closed"
        account = replacement.settings.app_id if protocol == "qq" else replacement.settings.client_id
        assert account == "789"
        await driver.stop_plugin(owner)
        await driver.stop_plugin(other)
        assert not adapter.declarations
        assert not adapter.started
        assert not adapter._streams
        assert not driver.channels
asyncio.run(main())
""",
    )


def test_invalid_dependency_config_fails_without_connections(tmp_path):
    run_script(
        tmp_path,
        """
owner = load_plugin("configured_bot", dependencies={"channels": {"qq": {"main": {"app_id": "123"}}}})
other = load_plugin("other_bot", dependencies={"undeclared": {}})
assert owner and other
async def main():
    async with driver.lifespan():
        assert owner.status.value == "failed"
        assert other.status.value == "failed"
        assert "secret_ref" in str(owner.failure)
        assert "undeclared" in str(other.failure)
        assert not driver.adapters
asyncio.run(main())
""",
    )


def test_disabled_qq_does_not_require_robot_credentials(tmp_path):
    run_script(
        tmp_path,
        """
import os
os.environ.pop("TEST_ROBOT_SECRET", None)
channels.config_values = {"qq": {"enabled": False}}
owner = load_plugin("configured_bot", dependencies=connections("qq", "123"))
assert owner
async def main():
    async with driver.lifespan():
        assert owner.ready, owner.failure
        adapter = driver.get_adapter("qq")
        assert len(adapter.declarations) == 1
        assert not adapter._streams
        assert not driver.channels
asyncio.run(main())
""",
    )


def test_ambiguous_connection_name_requires_protocol(tmp_path):
    run_script(
        tmp_path,
        """
values = connections("qq", "123")
values["channels"].update(connections("dingtalk", "456")["channels"])
owner = load_plugin("configured_bot", dependencies=values)
assert owner
async def main():
    async with driver.lifespan():
        assert owner.status.value == "failed"
        assert "Ambiguous connection" in str(owner.failure)
        assert not driver.adapters
asyncio.run(main())
""",
    )


def test_startup_failure_releases_created_connections(tmp_path):
    run_script(
        tmp_path,
        """
owner = load_plugin("configured_bot", dependencies=connections("qq", "123"))
assert owner
async def main():
    async with driver.lifespan():
        assert owner.status.value == "failed"
        adapter = driver.get_adapter("qq")
        assert not adapter.declarations
        assert not adapter._streams
        assert owner.cleanup_complete
asyncio.run(main())
""",
        CONSUMER
        + """
from opsmesh import on_startup
@on_startup
async def fail():
    raise RuntimeError("Injected startup failure")
""",
    )


def test_duplicate_robot_account_does_not_break_existing_consumer(tmp_path):
    run_script(
        tmp_path,
        """
owner = load_plugin("configured_bot", dependencies=connections("qq", "123"))
other = load_plugin("other_bot", dependencies=connections("qq", "123"))
async def main():
    async with driver.lifespan():
        assert owner.ready
        assert other.status.value == "failed"
        adapter = driver.get_adapter("qq")
        assert len(adapter.declarations) == 1
        await online(adapter.declarations[0])
asyncio.run(main())
""",
    )


def test_group_example_selects_native_agents_and_replies(tmp_path):
    run_script(
        tmp_path,
        """
import sys
from pathlib import Path
from types import SimpleNamespace
from opsmesh.plugins.channels import UserContext
sys.path.insert(0, str(Path("examples/groups").resolve()))
owner = load_plugin("group_assistant", config={"model": "test-model"}, dependencies=connections("qq", "123"))
assert owner
selected = []
replies = []
async def run(agent, message, *, context):
    selected.append((agent.name, message, context.id))
    return SimpleNamespace(final_output=agent.name)
owner.module.execution.run = run
async def send(event, message, **kwargs):
    replies.append(str(message))
async def main():
    async with driver.lifespan():
        channel = driver.get_adapter("qq").declarations[0]
        await online(channel)
        channel.send = send
        for group in ("group-a", "group-b", "ignored", None):
            result = await dispatch_event(channel, event(channel, group), matcher_filter=channel.accepts_matcher, invocation_context=UserContext(id="user"))
            assert not result.errors, result.errors
        assert selected == [("Sales assistant", "hello", "user"), ("Technical assistant", "hello", "user")]
        assert replies == ["Sales assistant", "Technical assistant"]
        assert len(driver.get_adapter("qq").declarations) == 1
asyncio.run(main())
""",
    )
