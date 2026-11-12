"""Load the conversation example as a plugin and inspect its declaration."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """

from opsmesh.drivers import base as driver_state
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import get_plugin, registry
from opsmesh.plugins.manager import PluginManager


driver_state.set_driver(Driver(Env(), Config()))
for module, values in {
    "opsmesh.plugins.channels": {"api": {"identities": [
        {"id": "smoke-user", "token_ref": "SMOKE_TOKEN"}
    ]}},
    "example_chat": {
        "model": "gpt-4o-mini",
        "reasoning": {"effort": "medium"},
        "qq": {"app_id": "12345", "secret_ref": "QQ_TEST_SECRET"},
    },
}.items():
    manager = PluginManager([module], config=values, namespace=module)
    registry.register_plugin_manager(manager)
plugin = manager.load_plugin("example_chat")
assert plugin is not None, "example_chat did not load"
assert plugin.metadata is not None
assert get_plugin("example_chat") is plugin
channels = get_plugin("channels")
assert channels is not None
assert get_plugin("api") is None
assert get_plugin("qq") is None
# Explicit manifest configuration belongs to channels, not its consumer.
from opsmesh.plugins.channels.qq.config import get_settings
channels.config_namespace = "opsmesh.plugins.channels"
channels.config_values = {"qq": {"concurrency": 3}}
plugin.config_namespace = "example_chat"
plugin.config_values = {"model": "different"}
token = registry._current_plugin.set(plugin)
try:
    assert get_settings().concurrency == 3
finally:
    registry._current_plugin.reset(token)
from opsmesh.plugins.channels.qq import connections
assert driver_state.get_driver().get_adapter("qq") is not None
assert driver_state.get_driver().get_adapter("qq").started is False
adapter = driver_state.get_driver().get_adapter("qq")
assert adapter.declarations[0].owner_id == plugin.id_
owned_startups = [
    entry
    for entry in driver_state.get_driver()._lifespan._startup_funcs
    if entry.plugin is channels
]
assert len(owned_startups) == 1, owned_startups

from opsmesh.plugins.channels.api import get_router

routes = [
    route
    for route in get_router().registrations
    if route.installation_id == "example_chat"
]
assert len(routes) == 7, routes
route = next(item for item in routes if item.relative_path == "/chat")
assert route.method == "POST"
assert route.path == "/plugins/example_chat/chat"
assert route.active is False, "the route must wait for plugin readiness"
assert route.body_model.__name__ == "ChatRequest"
assert route.response_adapter.json_schema()["title"] == "ChatReply"

from example_chat.agent import agent

assert agent.model == "gpt-4o-mini"
assert agent.model_settings.reasoning.effort == "medium"
print("loaded", plugin.id_, route.path)
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
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_example_chat_declares_an_inactive_route():
    assert run_example_script(SCRIPT) == (
        "loaded example_chat /plugins/example_chat/chat"
    )


BEHAVIOR_SCRIPT = """
import runpy
scenario = runpy.run_path("tests/fixtures/chat_scenario.py")["run_scenario"]
scenario(plugin, routes, connections)
"""


def test_example_chat_dispatches_commands_and_http_without_network():
    assert run_example_script(SCRIPT + BEHAVIOR_SCRIPT).endswith(
        "conversations and commands verified"
    )
