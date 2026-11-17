"""Verify protocol selection and rollback through the real plugin loader."""

import os
import subprocess
import sys
from pathlib import Path

BOOTSTRAP = """
import asyncio
import sys
from opsmesh.drivers import base as driver_state
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.manager import PluginManager

settings = {
    "qq": {"concurrency": "invalid-unused-setting"},
    "dingtalk": "invalid-unused-section",
}
driver = Driver(Env(), Config())
driver_state.set_driver(driver)
manager = PluginManager(
    ["opsmesh.plugins.channels"], config=settings,
)
registry.register_plugin_manager(manager)
channels = manager.load_plugin("opsmesh.plugins.channels")
assert channels is not None
manager = PluginManager(["consumer", "failed"])
registry.register_plugin_manager(manager)
assert all("opsmesh.plugins.channels." + name not in sys.modules
           for name in ("api", "qq", "dingtalk"))
from opsmesh.plugins.channels.lifecycle.hooks import transports
transports = transports
"""


def run_script(tmp_path, script):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["API_TEST_TOKEN"] = "test-only-token"
    environment["QQ_SECRET"] = "0123456789abcdef0123456789abcdef"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(tmp_path)]
    )
    result = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP + script],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_config_or_declarations_starts_no_transports(tmp_path):
    run_script(
        tmp_path,
        """
channels.config_values = settings
async def main():
    async with driver.lifespan():
        assert not transports._started
        assert "uvicorn" not in sys.modules
asyncio.run(main())
""",
    )


def test_api_declaration_ignores_unused_settings_and_rolls_back(tmp_path):
    source = """
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")
from opsmesh import on_message
hello = on_message(block=True)
from opsmesh.plugins.channels import UserContext
async def identity():
    return UserContext("anonymous")
channels.api.message(handlers=(hello,), identity=identity)

@hello.handle()
async def reply():
    await hello.finish("hello")
"""
    (tmp_path / "consumer.py").write_text(source)
    (tmp_path / "failed.py").write_text(
        source + '\nraise RuntimeError("Injected declaration failure")\n'
    )
    run_script(
        tmp_path,
        """
manager.dependency_config_values = {
    "channels": {"api": {"main": {"enabled": False}}}
}
consumer = manager.load_plugin("consumer")
assert consumer is not None
assert manager.load_plugin("failed") is None
assert not transports._declared
assert "opsmesh.plugins.channels.qq" not in sys.modules
assert "opsmesh.plugins.channels.dingtalk" not in sys.modules
from opsmesh.plugins.channels.api import get_entrances
assert len(get_entrances().entries) == 1
async def main():
    async with driver.lifespan():
        entrance = transports._started[0]
        assert entrance.started
        try:
            registry.revoke_plugin(consumer)
        except RuntimeError as error:
            assert "stopped before revocation" in str(error)
        else:
            raise AssertionError("Running plugin revocation was accepted")
        await manager.stop_plugin("consumer")
        registry.revoke_plugin(consumer)
        assert not get_entrances().entries
        assert not entrance.started
        assert not entrance._mounted
asyncio.run(main())
assert not transports._declared
assert not get_entrances().entries
""",
    )


def test_qq_declaration_needs_no_api_or_dingtalk_configuration(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")

""")
    run_script(
        tmp_path,
        """
manager.dependency_config_values = {"channels": {"qq": {"primary": {
    "app_id": "12345", "secret_ref": "QQ_SECRET"
}}}}
settings["qq"] = {"enabled": False, "concurrency": 2}
channels.config_values = settings
consumer = manager.load_plugin("consumer")
assert consumer is not None
assert "opsmesh.plugins.channels.api" not in sys.modules
assert "opsmesh.plugins.channels.dingtalk" not in sys.modules
async def main():
    async with driver.lifespan():
        connection = driver.get_adapter("qq").declarations[0]
        assert not connection.ready
        assert len(transports._started) == 1
        assert driver.get_adapter("qq").started
    assert not connection.ready
asyncio.run(main())
registry.revoke_plugin(consumer)
assert not transports._declared
""",
    )


def test_configured_http_requires_consumer_message_binding(tmp_path):
    (tmp_path / "consumer.py").write_text(
        "from opsmesh import require\nrequire('channels')\n"
    )
    run_script(
        tmp_path,
        """
manager.dependency_config_values = {
    "channels": {"api": {"main": {"enabled": False}}}
}
consumer = manager.load_plugin("consumer")
consumer.required = True
async def main():
    try:
        async with driver.lifespan():
            raise AssertionError("Missing message binding was accepted")
    except ValueError:
        pass
    assert not transports._started
asyncio.run(main())
""",
    )


def test_dingtalk_stream_needs_no_http_or_api(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")

""")
    run_script(
        tmp_path,
        """
import os
os.environ["DINGTALK_SECRET"] = "test-secret"
manager.dependency_config_values = {"channels": {"dingtalk": {"primary": {
    "client_id": "test", "client_secret_ref": "DINGTALK_SECRET"
}}}}
settings.clear()

channels.config_values = settings
consumer = manager.load_plugin("consumer")
assert consumer is not None
from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream
async def receive(stream):
    assert consumer.ready
    stream.adapter.stream_connected(stream.channel)
    await asyncio.Event().wait()
DingTalkStream._receive = receive
async def main():
    async with driver.lifespan():
        connection = driver.get_adapter("dingtalk").declarations[0]
        for _ in range(100):
            if connection.ready:
                break
            await asyncio.sleep(0)
        assert connection.ready
        assert "uvicorn" not in sys.modules
        from inspect import getattr_static
        assert getattr_static(connection, "path", None) is None
    assert connection.state == "closed"
    assert not driver.channels
asyncio.run(main())
""",
    )


def test_consumer_http_listeners_restart_independently(tmp_path):
    source = """
from opsmesh import on_message, require
channels = require("channels")
hello = on_message(block=True)
async def identity():
    return channels.UserContext(__name__)
@hello.handle()
async def reply():
    await hello.finish(__name__)
channels.api.message(handlers=(hello,), identity=identity)
"""
    for name in ("consumer", "second"):
        (tmp_path / f"{name}.py").write_text(source)
    run_script(
        tmp_path,
        """
import httpx
manager.dependency_config_values = {
    "channels": {"api": {"main": {"port": 0}}}
}
consumer = manager.load_plugin("consumer")
second_manager = PluginManager(["second"], dependencies={
    "channels": {"api": {"main": {"port": 0}}}
})
registry.register_plugin_manager(second_manager)
second = second_manager.load_plugin("second")
assert consumer is not None and second is not None
async def request(channel, plugin_id):
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{channel.server.bound_port}",
        trust_env=False,
    ) as client:
        return await client.post(f"/plugins/{plugin_id}/messages",
                                 json={"message": "hello"})
async def main():
    async with driver.lifespan():
        first = driver.get_channel("api:consumer:main")
        other = driver.get_channel("api:second:main")
        assert first is not other
        assert first.server.bound_port != other.server.bound_port
        assert (await request(first, "consumer")).status_code == 200
        assert (await request(first, "second")).status_code == 404
        assert (await request(other, "second")).status_code == 200
        await manager.stop_plugin("consumer")
        assert not first.started
        assert other.started
        assert (await request(other, "second")).status_code == 200
        await manager.start_plugin("consumer")
        restarted = driver.get_channel("api:consumer:main")
        assert restarted is not first
        assert (await request(restarted, "consumer")).status_code == 200
        assert len(transports._started) == 2
    assert not driver.channels
asyncio.run(main())
""",
    )
