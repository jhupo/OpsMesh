"""Load the conversation example as a plugin and exercise its connections."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import get_plugin, registry
from opsmesh.plugins.manager import PluginManager, register_plugin_manager

driver = Driver(Env(), Config(command_start={"/", "!"}))
for module, values in {
    "opsmesh.plugins.channels": {},
    "example_chat": {
        "model": "gpt-4o-mini",
        "reasoning": {"effort": "medium"},
    },
}.items():
    manager = PluginManager(
        [module],
        config=values,
    )
    register_plugin_manager(manager)
plugin = manager.load_plugin("example_chat")
assert plugin is not None, "example_chat did not load"
assert plugin.metadata is not None
assert get_plugin("example_chat") is plugin
assert get_plugin("channels") is not None
assert get_plugin("api") is None
assert get_plugin("qq") is None

from opsmesh.plugins.channels import ChannelConnections
from opsmesh.plugins.channels.lifecycle import protocol_adapter
from opsmesh.plugins.channels.lifecycle import register_bindings

connections = ChannelConnections.model_validate({
    "api": {"main": {"port": 0}},
    "qq": {"primary": {"app_id": "12345", "secret_ref": "QQ_TEST_SECRET"}},
})
register_bindings(plugin, lambda: connections)
assert sorted(connections.api) == ["main"]
assert sorted(connections.qq) == ["primary"]

for protocol in ("api", "qq"):
    try:
        protocol_adapter(driver, protocol)
    except LookupError:
        pass
    else:
        raise AssertionError("connections must wait for plugin startup")

from example_chat import agent as definition
assert not hasattr(definition, "agent"), "Agent resources start in on_startup"
print("loaded", plugin.id_)
"""


BEHAVIOR_SCRIPT = """
import anyio
import os
import runpy

os.environ["QQ_TEST_SECRET"] = "test-secret"

from example_chat import lifecycle
from opsmesh.plugins.channels.qq.gateway import QQStream

scenario = runpy.run_path("tests/fixtures/chat_scenario.py")["run_scenario"]
run = runpy.run_path("tests/fixtures/consumer_lifecycle.py")["run"]


async def receive(stream):
    stream.channel.inbound_task_connected()
    try:
        with anyio.CancelScope() as scope:
            stream._scope = scope
            if stream._stopping:
                return
            await anyio.sleep_forever()
    finally:
        stream._scope = None
        stream.channel.inbound_task_finished()
        stream._finished.set()


QQStream.run = receive


async def body():
    api = protocol_adapter(driver, "api").get_connection(plugin.id_, "main")
    await scenario(plugin, api)


def skip(func):
    return (
        func is lifecycle.startup
        or func.__module__ == "opsmesh.plugins.database.schema"
    )


anyio.run(lambda: run(plugin, driver, body, skip=skip, started=("channels",)))
"""


def run_example_script(script):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "src"),
            str(ROOT / "examples" / "common"),
            str(ROOT / "examples" / "chat"),
            env.get("PYTHONPATH", ""),
        ]
    )
    env.pop("OPENAI_API_KEY", None)
    payload = script
    managed = (
        "from opsmesh.plugins import plugin_environment\n"
        f"with plugin_environment(): exec({payload!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", managed],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_example_chat_exposes_connections_after_startup():
    assert run_example_script(SCRIPT) == "loaded example_chat"


def test_example_chat_dispatches_commands_and_http_without_network():
    assert run_example_script(SCRIPT + BEHAVIOR_SCRIPT).endswith(
        "conversations and commands verified"
    )
