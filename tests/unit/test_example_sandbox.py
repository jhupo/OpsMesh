"""Load the sandbox example without creating a runtime workspace."""

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
manager = PluginManager(["example_sandbox"], config={"model": "gpt-test"})
registry.register_plugin_manager(manager)
plugin = manager.load_plugin("example_sandbox")
assert plugin is not None
assert get_plugin("channels") is not None
assert get_plugin("example_sandbox") is plugin
from opsmesh.plugins.channels.api import get_router
routes = [route for route in get_router().registrations
          if route.installation_id == plugin.id_]
assert len(routes) == 1
assert routes[0].path == "/plugins/example_sandbox/messages"
from example_sandbox import agent as definition
assert not hasattr(definition, "agent")
print("loaded", plugin.id_, routes[0].path)
"""


def run_sandbox_script(script):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "src"),
            str(ROOT / "examples" / "common"),
            str(ROOT / "examples" / "sandbox"),
            environment.get("PYTHONPATH", ""),
        ]
    )
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_sandbox_example_loads_without_starting_a_sandbox():
    assert (
        run_sandbox_script(SCRIPT)
        == "loaded example_sandbox /plugins/example_sandbox/messages"
    )


def test_sandbox_sessions_commands_and_workspace_lifetime():
    scenario = """
import asyncio
from agents.sandbox import SandboxAgent
asyncio.run(definition.startup())
assert isinstance(definition.agent, SandboxAgent)
import runpy
scenario = runpy.run_path("tests/fixtures/sandbox_scenario.py")
scenario["run_scenario"](plugin, routes)
"""
    assert run_sandbox_script(SCRIPT + scenario).endswith(
        "sandbox conversations verified"
    )
