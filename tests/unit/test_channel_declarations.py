"""Verify protocol selection and rollback through the real plugin loader."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BOOTSTRAP = """
import asyncio
import sys
from opsmesh.framework import runtime
from opsmesh.framework.config import Config, Env
from opsmesh.drivers.http import HTTPDriver
from opsmesh.framework.plugins import registry
from opsmesh.framework.plugins.manager import PluginManager

settings = {
    "api": {"identities": [{"id": "test", "token_ref": "API_TEST_TOKEN"}]},
    "qq": {"concurrency": "invalid-unused-setting"},
    "dingtalk": "invalid-unused-section",
}
driver = HTTPDriver(Env(), Config(
    driver_options={"http": {"enabled": False}},
    plugin_configs={"opsmesh_channels": settings},
))
runtime.set_driver(driver)
manager = PluginManager(["opsmesh_channels", "consumer", "failed"])
registry.register_plugin_manager(manager)
channels = manager.load_plugin("opsmesh_channels")
assert channels is not None
assert all("opsmesh_channels." + name not in sys.modules
           for name in ("api", "qq", "dingtalk"))
from opsmesh_channels.lifecycle import transports
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
        assert not driver.channels
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
driver.config.plugin_configs["opsmesh_channels"] = settings
async def main():
    async with driver.lifespan():
        assert not transports._started
asyncio.run(main())
""",
    )


def test_api_declaration_ignores_unused_settings_and_rolls_back(tmp_path):
    source = """
from opsmesh.framework.plugins import require
channels = require("opsmesh_channels")
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
assert "opsmesh_channels.qq" not in sys.modules
assert "opsmesh_channels.dingtalk" not in sys.modules
from opsmesh_channels.api import get_router
assert len(get_router().registrations) == 1
async def main():
    async with driver.lifespan():
        entrance = transports._started[0]
        assert entrance.started
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
from opsmesh.framework.plugins import require
channels = require("opsmesh_channels")
qq = channels.qq
connection = qq.declare_connection(
    qq.QQConnectionSettings(app_id="12345", secret_ref="QQ_SECRET"),
    name="primary",
)
""")
    run_script(
        tmp_path,
        """
settings["qq"] = {"concurrency": 2}
settings.pop("api")
driver.config.plugin_configs["opsmesh_channels"] = settings
consumer = manager.load_plugin("consumer")
assert consumer is not None
assert "opsmesh_channels.api" not in sys.modules
assert "opsmesh_channels.dingtalk" not in sys.modules
async def main():
    async with driver.lifespan():
        assert consumer.module.connection.ready
        assert len(transports._started) == 1
    assert not consumer.module.connection.ready
asyncio.run(main())
registry.revoke_plugin(consumer)
assert not transports._declared
""",
    )


@pytest.mark.parametrize("section", [None, {"identities": []}])
def test_declared_api_requires_valid_authentication(tmp_path, section):
    (tmp_path / "consumer.py").write_text("""
from opsmesh.framework.plugins import require
channels = require("opsmesh_channels")
@channels.api.get("/hello")
async def hello() -> str:
    return "hello"
""")
    run_script(
        tmp_path,
        f"""
settings["api"] = {section!r}
driver.config.plugin_configs["opsmesh_channels"] = settings
channels.required = True
assert manager.load_plugin("consumer") is not None
from opsmesh_channels.api.errors import APIConfigurationError
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
