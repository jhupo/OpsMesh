"""Verify connection preparation and rollback through the real loader."""

import os
import subprocess
import sys
from pathlib import Path

BOOTSTRAP = """
import asyncio
import sys

import anyio
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.manager import PluginManager, register_plugin_manager

driver = Driver(Env(), Config())
manager = PluginManager(["opsmesh.plugins.channels"])
register_plugin_manager(manager)
provider = manager.load_plugin("opsmesh.plugins.channels")
assert provider is not None
from opsmesh.plugins.channels.lifecycle import (
    _active_adapters, protocol_adapter,
)
"""


def run_script(tmp_path, script):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.pop("DINGTALK_SECRET", None)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(tmp_path)]
    )
    payload = BOOTSTRAP + script
    managed = (
        "from opsmesh.plugins import plugin_environment\n"
        f"with plugin_environment(): exec({payload!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", managed],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def load_consumer(dependencies):
    return f"""
manager = PluginManager(["consumer"], dependencies={dependencies!r})
register_plugin_manager(manager)
consumer = manager.load_plugin("consumer")
assert consumer is not None
"""


def test_no_configuration_creates_no_adapter_or_connection(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
require("channels")
""")
    run_script(
        tmp_path,
        load_consumer({})
        + """
async def main():
    async with driver.lifespan():
        assert not _active_adapters[driver]
        assert "uvicorn" not in sys.modules
asyncio.run(main())
assert "uvicorn" not in sys.modules
""",
    )


def test_api_connection_is_prepared_before_listening(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh import on_message, on_ready, require
require("channels")
from opsmesh.plugins.channels.api import get_app

hello = on_message(block=True)


@hello.handle()
async def reply():
    await hello.finish("hello")


@on_ready(with_driver=True)
async def configure_app(driver):
    assert get_app(driver) is not None
""")
    run_script(
        tmp_path,
        load_consumer({"channels": {"api": {"main": {"port": 0}}}})
        + """
from opsmesh.plugins.channels.api import APIChannel, get_app
assert "opsmesh.plugins.channels.api.adapter" not in sys.modules
try:
    get_app(driver)
except RuntimeError as error:
    assert "not prepared" in str(error)
else:
    raise AssertionError("Prepared connections were available before startup")
async def main():
    async with driver.lifespan():
        adapter = protocol_adapter(driver, "api")
        channel = adapter.get_connection("consumer", "main")
        assert isinstance(channel, APIChannel)
        assert channel.ready
        assert channel.server.bound_port is not None
        assert ("POST", "/plugins/{plugin_id}/messages") in channel.mounted
        assert ("GET", "/attachments/{plugin_id}/{attachment_id}") in (
            channel.mounted
        )
        assert "uvicorn" in sys.modules
    assert channel.state == "closed"
    assert adapter.connections == ()
asyncio.run(main())
""",
    )


def test_failed_preparation_releases_prepared_connections(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
require("channels")
""")
    run_script(
        tmp_path,
        load_consumer(
            {
                "channels": {
                    "api": {"main": {"port": 0}},
                    "dingtalk": {
                        "primary": {
                            "client_id": "test",
                            "client_secret_ref": "MISSING_DINGTALK_SECRET",
                        }
                    },
                }
            }
        )
        + """
from opsmesh.plugins.model import PluginStatus
assert consumer.status is PluginStatus.LOADED
async def main():
    async with driver.lifespan():
        assert consumer.status is PluginStatus.FAILED
        adapter = protocol_adapter(driver, "api")
        assert adapter.connections == ()
        assert "uvicorn" not in sys.modules
    assert adapter.connections == ()
    assert driver not in _active_adapters
asyncio.run(main())
""",
    )


def test_qq_connection_does_not_load_other_protocols(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
require("channels")
""")
    run_script(
        tmp_path,
        """
import os
os.environ["QQ_SECRET"] = "0123456789abcdef0123456789abcdef"
"""
        + load_consumer(
            {
                "channels": {
                    "qq": {
                        "primary": {
                            "app_id": "12345",
                            "secret_ref": "QQ_SECRET",
                        }
                    }
                }
            }
        )
        + """
from opsmesh.plugins.channels.qq import QQChannel
from opsmesh.plugins.channels.qq.gateway import QQStream

async def run(stream):
    stream.channel.inbound_task_connected()
    with anyio.CancelScope() as scope:
        stream._scope = scope
        await anyio.sleep_forever()
    stream.channel.inbound_task_finished()
    stream._finished.set()


def stop(stream):
    if stream._scope is not None:
        stream._scope.cancel()


async def wait_closed(stream):
    await stream._finished.wait()


QQStream.run = run
QQStream.stop = stop
QQStream.wait_closed = wait_closed
assert "opsmesh.plugins.channels.api.adapter" not in sys.modules
assert "opsmesh.plugins.channels.dingtalk.adapter" not in sys.modules
async def waits_until_ready(channel):
    for _ in range(100):
        if channel.ready:
            return
        await asyncio.sleep(0)
    raise AssertionError("Connection did not become ready")


async def main():
    async with driver.lifespan():
        adapter = protocol_adapter(driver, "qq")
        channel = adapter.get_connection("consumer", "primary")
        await waits_until_ready(channel)
        assert isinstance(channel, QQChannel)
        assert channel.ready
        assert set(_active_adapters[driver]) == {"qq"}
        assert "uvicorn" not in sys.modules
    assert channel.state == "closed"
    assert adapter.connections == ()
asyncio.run(main())
""",
    )


def test_dingtalk_stream_needs_no_http_listener(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
require("channels")
""")
    run_script(
        tmp_path,
        """
import os
os.environ["DINGTALK_SECRET"] = "test-secret"
"""
        + load_consumer(
            {
                "channels": {
                    "dingtalk": {
                        "primary": {
                            "client_id": "test",
                            "client_secret_ref": "DINGTALK_SECRET",
                        }
                    }
                }
            }
        )
        + """
from opsmesh.plugins.channels.dingtalk.channel import DingTalkChannel
from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream

async def receive(stream):
    stream.channel.inbound_task_connected()
    await anyio.sleep_forever()


DingTalkStream._receive = receive
async def waits_until_ready(channel):
    for _ in range(100):
        if channel.ready:
            return
        await asyncio.sleep(0)
    raise AssertionError("Connection did not become ready")


async def main():
    async with driver.lifespan():
        adapter = protocol_adapter(driver, "dingtalk")
        channel = adapter.get_connection("consumer", "primary")
        await waits_until_ready(channel)
        assert isinstance(channel, DingTalkChannel)
        assert channel.ready
        assert "uvicorn" not in sys.modules
        from inspect import getattr_static
        assert getattr_static(channel, "path", None) is None
    assert channel.state == "closed"
    assert adapter.connections == ()
asyncio.run(main())
""",
    )


def test_consumer_listeners_stop_and_restart_independently(tmp_path):
    source = """
from opsmesh import on_message, require
require("channels")
hello = on_message(block=True)


@hello.handle()
async def reply():
    await hello.finish(__name__)
"""
    for name in ("consumer", "second"):
        (tmp_path / f"{name}.py").write_text(source)
    run_script(
        tmp_path,
        """
import httpx
"""
        + load_consumer({"channels": {"api": {"main": {"port": 0}}}})
        + """
second_manager = PluginManager(
    ["second"], dependencies={"channels": {"api": {"main": {"port": 0}}}}
)
register_plugin_manager(second_manager)
second = second_manager.load_plugin("second")
assert second is not None
async def request(channel, plugin_id):
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{channel.server.bound_port}",
        trust_env=False,
    ) as client:
        return await client.post(
            f"/plugins/{plugin_id}/messages",
            json={"user": {"id": plugin_id}, "message": "hello"},
        )
async def main():
    async with driver.lifespan():
        adapter = protocol_adapter(driver, "api")
        first = adapter.get_connection("consumer")
        other = adapter.get_connection("second")
        assert first is not other
        assert first.server.bound_port != other.server.bound_port
        assert (await request(first, "consumer")).status_code == 200
        assert (await request(first, "second")).status_code == 404
        assert (await request(other, "second")).status_code == 200
        await driver.stop_plugin(consumer)
        assert first.state == "closed"
        assert not first.ready
        assert other.ready
        assert (await request(other, "second")).status_code == 200
        await driver.start_plugin(consumer)
        restarted = adapter.get_connection("consumer")
        assert restarted is not first
        assert (await request(restarted, "consumer")).status_code == 200
        assert len(adapter.connections) == 2
        await driver.stop_plugin(consumer)
        await driver.stop_plugin(second)
        await driver.stop_plugin(provider)
        assert driver not in _active_adapters
        assert not adapter.connections
        await driver.start_plugin(provider)
        await driver.start_plugin(second)
        replacement = protocol_adapter(driver, "api")
        assert replacement is not adapter
        new_channel = replacement.get_connection("second")
        assert replacement.connections == (new_channel,)
        assert (await request(new_channel, "second")).status_code == 200
    assert driver not in _active_adapters
asyncio.run(main())
""",
    )
