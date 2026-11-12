"""Load the sandbox example without creating a runtime workspace."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import logging
from opsmesh.framework import runtime
from opsmesh.framework.config import Config, Env
from opsmesh.framework.drivers import Driver
from opsmesh.framework.plugins import get_plugin, registry
from opsmesh.framework.plugins.manager import PluginManager

class SmokeDriver(Driver):
    @property
    def type(self):
        return "smoke"
    @property
    def logger(self):
        return logging.getLogger("smoke")

runtime.set_driver(SmokeDriver(Env(), Config(plugin_configs={
    "example_sandbox": {"model": "gpt-test"},
    "opsmesh_channels": {},
})))
manager = PluginManager(["example_sandbox"])
registry._managers.append(manager)
plugin = manager.load_plugin("example_sandbox")
assert plugin is not None
assert get_plugin("opsmesh_channels") is not None
assert get_plugin("example_sandbox") is plugin
from opsmesh_channels.api import get_router
routes = [route for route in get_router().registrations
          if route.installation_id == plugin.id_]
assert len(routes) == 8
assert routes[0].path == "/plugins/example_sandbox/chat"
from example_sandbox.agent import agent
from agents.sandbox import SandboxAgent
assert isinstance(agent, SandboxAgent)
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
        == "loaded example_sandbox /plugins/example_sandbox/chat"
    )


def test_sandbox_sessions_commands_and_workspace_lifetime():
    scenario = """
import runpy
scenario = runpy.run_path("tests/fixtures/sandbox_scenario.py")
scenario["run_scenario"](plugin, routes)
"""
    assert run_sandbox_script(SCRIPT + scenario).endswith(
        "sandbox conversations verified"
    )
