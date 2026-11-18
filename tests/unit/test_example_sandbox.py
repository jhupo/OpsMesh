"""Load the sandbox example without creating a runtime workspace."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import tempfile
from pathlib import Path

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import get_dependency_config, get_plugin, registry
from opsmesh.plugins.manager import PluginManager, register_plugin_manager
from opsmesh.plugins.registry import plugin_scope

driver = Driver(Env(), Config())
temporary = tempfile.TemporaryDirectory()
register_plugin_manager(
    PluginManager(
        ["opsmesh.plugins.filesystem"],
        config={"root": str(Path(temporary.name) / "files")},
    )
)
manager = PluginManager(
    ["example_sandbox"],
    config={"model": "gpt-test"},
    dependencies={"channels": {"api": {"main": {"port": 0}}}},
)
register_plugin_manager(manager)
plugin = manager.load_plugin("example_sandbox")
assert plugin is not None
assert get_plugin("channels") is not None
assert get_plugin("example_sandbox") is plugin

from opsmesh.plugins.channels import ChannelConnections

with plugin_scope(plugin):
    connections = get_dependency_config(ChannelConnections)
assert sorted(connections.api) == ["main"]

try:
    driver.get_adapter("api")
except KeyError:
    pass
else:
    raise AssertionError("connections must wait for plugin startup")

from example_sandbox import agent as definition
assert not hasattr(definition, "agent")
print("loaded", plugin.id_)
"""


SCENARIO = """
import anyio
import runpy

from example_sandbox import lifecycle

run = runpy.run_path("tests/fixtures/consumer_lifecycle.py")["run"]


async def body():
    api = driver.get_adapter("api").get_connection(plugin.id_, "main")
    scenario = runpy.run_path("tests/fixtures/sandbox_scenario.py")
    await scenario["run_scenario"](plugin, api)


def skip(func):
    return (
        func is lifecycle.startup
        or func.__module__ == "opsmesh.plugins.database.schema"
    )


anyio.run(
    lambda: run(
        plugin, driver, body, skip=skip, started=("filesystem",)
    )
)
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
    payload = script
    managed = (
        "from opsmesh.plugins import plugin_environment\n"
        f"with plugin_environment(): exec({payload!r})"
    )
    result = subprocess.run(
        [sys.executable, "-c", managed],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_sandbox_example_loads_without_starting_a_sandbox():
    assert run_sandbox_script(SCRIPT) == "loaded example_sandbox"


def test_sandbox_sessions_commands_and_workspace_lifetime():
    assert run_sandbox_script(SCRIPT + SCENARIO).endswith(
        "sandbox conversations verified"
    )
