"""Check automatic schema ownership and startup ordering in a fresh host."""

import os
import subprocess
import sys
from pathlib import Path


def test_schema_declaration_binds_checks_and_rolls_back_failed_plugin(tmp_path):
    source = """
from pathlib import Path
from opsmesh import on_startup, on_plugin_loaded
from opsmesh.plugins import PluginMetadata, require

database = require("opsmesh.plugins.database")

@on_startup
async def startup():
    events.append("consumer")

events = []
__plugin_meta__ = PluginMetadata(
    name="Example", description="Example", usage="Example",
    extra={"database": database.DatabaseSchema(
        Path(__file__).with_name("migrations"), "schema_probe_001",
    )},
)
"""
    for name in ["schema_probe", "schema_failure"]:
        package = tmp_path / name
        (package / "migrations").mkdir(parents=True)
        (package / "migrations" / "initial.py").write_text(
            f"revision = '{name}_001'\ndown_revision = None\n"
        )
        content = source.replace("schema_probe_001", name + "_001")
        if name == "schema_failure":
            content += """
@on_plugin_loaded
def reject(plugin):
    if plugin.id_ == "schema_failure":
        raise RuntimeError("Injected late load failure")
"""
        (package / "__init__.py").write_text(content)

    script = """
import asyncio
import logging
from opsmesh.drivers import base as driver_state
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins import registry
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.hooks import _bound

class TestDriver(Driver):
    @property
    def type(self): return "test"
    @property
    def logger(self): return logging.getLogger("test")

driver_state.set_driver(TestDriver(Env(), Config()))
manager = PluginManager(["schema_probe", "schema_failure"])
registry._managers.append(manager)
plugin = manager.load_plugin("schema_probe")
assert plugin is not None
from opsmesh.plugins.database import migrations, schema as database_schema
assert plugin.dependencies == ["database"]
schema = plugin.metadata.extra["database"]
assert database_schema.registry.get("schema_probe").head == schema.head
migrations.validate_migrations("schema_probe")
startup_entries = driver_state.get_driver()._lifespan._startup_funcs
owned = [item for item in startup_entries if item.plugin is plugin]
assert len(owned) == 2

async def check(owner):
    assert owner == "schema_probe"
    plugin.module.events.append("schema")

migrations.check_migrations = check
async def startup():
    for item in owned:
        await item.func()
asyncio.run(startup())
assert plugin.module.events == ["schema", "consumer"]
count = len(_bound)
assert manager.load_plugin("schema_failure") is None
assert len(_bound) == count
assert all(item.owner != "schema_failure"
           for item in database_schema.registry.sources())
assert not any(
    item.plugin and item.plugin.id_ == "schema_failure"
    for item in startup_entries
)
registry.revoke_plugin(plugin)
assert not database_schema.registry.sources()
assert not any(item.plugin is plugin for item in startup_entries)
provider = registry.get_plugin("database")
registry.revoke_plugin(provider)
assert not _bound
print("schema ownership verified")
"""
    environment = dict(os.environ)
    root = Path(__file__).resolve().parents[2]
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(tmp_path)]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "schema ownership verified"
