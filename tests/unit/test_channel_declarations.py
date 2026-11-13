"""Verify protocol selection and rollback through the real plugin loader."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BOOTSTRAP = """
import asyncio
import sys
from opsmesh.drivers import base as driver_state
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.manager import PluginManager

settings = {
    "api": {"enabled": False, "identities": [
        {"id": "test", "token_ref": "API_TEST_TOKEN"}]},
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
from opsmesh.plugins.channels.lifecycle.coordinator import channel_lifecycle
transports = channel_lifecycle.transports
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


def test_configured_api_starts_without_business_routes(tmp_path):
    run_script(
        tmp_path,
        """
async def main():
    async with driver.lifespan():
        assert len(transports._started) == 1
        entrance = transports._started[0]
        assert entrance.started
        assert ("GET", "/healthz") in entrance._mounted
        assert not entrance._router.registrations
        assert driver.get_channel("api") is entrance
    assert not entrance.started
    assert not entrance._mounted
asyncio.run(main())
""",
    )


def test_no_config_or_declarations_starts_no_transports(tmp_path):
    run_script(
        tmp_path,
        """
settings.pop("api")
channels.config_values = settings
async def main():
    async with driver.lifespan():
        assert not transports._started
        assert not hasattr(channel_lifecycle, "http")
        assert "uvicorn" not in sys.modules
asyncio.run(main())
""",
    )


def test_api_declaration_ignores_unused_settings_and_rolls_back(tmp_path):
    source = """
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")
@channels.api.get("/hello")
async def hello() -> str:
    return "hello"
"""
    (tmp_path / "consumer.py").write_text(source)
    (tmp_path / "failed.py").write_text(
        source + '\nraise RuntimeError("Injected declaration failure")\n'
    )
    run_script(
        tmp_path,
        """
consumer = manager.load_plugin("consumer")
assert consumer is not None
assert manager.load_plugin("failed") is None
assert not transports._declared
assert "opsmesh.plugins.channels.qq" not in sys.modules
assert "opsmesh.plugins.channels.dingtalk" not in sys.modules
from opsmesh.plugins.channels.api import get_router
assert len(get_router().registrations) == 1
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
        assert not get_router().registrations
        assert entrance.started
        assert ("GET", "/healthz") in entrance._mounted
asyncio.run(main())
assert not transports._declared
assert not get_router().registrations
""",
    )


def test_qq_declaration_needs_no_api_or_dingtalk_configuration(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")
qq = channels.qq
connection = qq.declare_connection(
    qq.QQConnectionSettings(app_id="12345", secret_ref="QQ_SECRET"),
    name="primary",
)
""")
    run_script(
        tmp_path,
        """
settings["qq"] = {"concurrency": 2, "host": "127.0.0.1", "port": 0}
settings.pop("api")
channels.config_values = settings
consumer = manager.load_plugin("consumer")
assert consumer is not None
assert "opsmesh.plugins.channels.api" not in sys.modules
assert "opsmesh.plugins.channels.dingtalk" not in sys.modules
async def main():
    async with driver.lifespan():
        assert consumer.module.connection.ready
        assert len(transports._started) == 1
        assert driver.get_adapter("qq").server.bound_port > 0
    assert not consumer.module.connection.ready
    assert not hasattr(channel_lifecycle, "http")
asyncio.run(main())
registry.revoke_plugin(consumer)
assert not transports._declared
""",
    )


@pytest.mark.parametrize("section", [None, {"identities": []}])
def test_declared_api_requires_valid_authentication(tmp_path, section):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")
@channels.api.get("/hello")
async def hello() -> str:
    return "hello"
""")
    run_script(
        tmp_path,
        f"""
settings["api"] = {section!r}
channels.config_values = settings
channels.required = True
assert manager.load_plugin("consumer") is not None
from opsmesh.plugins.channels.api.errors import APIConfigurationError
async def main():
    try:
        async with driver.lifespan():
            raise AssertionError("Invalid API settings were accepted")
    except (ValueError, APIConfigurationError):
        pass
    assert not transports._started
asyncio.run(main())
""",
    )


def test_dingtalk_stream_needs_no_http_or_api(tmp_path):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.plugins import require
channels = require("opsmesh.plugins.channels")
dingtalk = channels.dingtalk
connection = dingtalk.declare_connection(
    dingtalk.DingTalkConnectionSettings(
        client_id="test", client_secret_ref="DINGTALK_SECRET"
    ),
    name="primary",
)
""")
    run_script(
        tmp_path,
        """
import os
os.environ["DINGTALK_SECRET"] = "test-secret"
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
        for _ in range(100):
            if consumer.module.connection.ready:
                break
            await asyncio.sleep(0)
        assert consumer.module.connection.ready
        assert not hasattr(channel_lifecycle, "http")
        assert "uvicorn" not in sys.modules
        from inspect import getattr_static
        assert getattr_static(consumer.module.connection, "path", None) is None
    assert consumer.module.connection.state == "closed"
    assert not driver.channels
asyncio.run(main())
""",
    )
