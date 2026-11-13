"""Check persisted plugin configuration and host ownership boundaries."""

import asyncio
import json
import sqlite3
import sys

import pytest
from pydantic import ValidationError

from opsmesh import app as host
from opsmesh.drivers import base as driver_state
from opsmesh.plugins import admission, get_plugin, registry
from opsmesh.plugins import load as plugin_load
from opsmesh.plugins import manager as plugin_manager
from opsmesh.plugins.errors import PluginProtectedError
from opsmesh.plugins.model import PluginStatus
from opsmesh.plugins.storage import PluginRecord, PluginStorage


@pytest.fixture
def host_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_state, "_driver", None)
    monkeypatch.setattr(registry, "_plugins", {})
    managers = []
    monkeypatch.setattr(registry, "_managers", managers)
    monkeypatch.setattr(plugin_manager, "_managers", managers)
    monkeypatch.setattr(plugin_load, "_managers", managers)
    monkeypatch.setattr(host, "entry_points", lambda **kwargs: ())
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    admission.clear_plugin_admission()
    for name in tuple(sys.modules):
        if name.startswith("host_test_"):
            del sys.modules[name]


def register(directory, module, source, **options):
    (directory / f"{module}.py").write_text(source, encoding="utf-8")
    with PluginStorage(directory / "data") as storage:
        storage.save(PluginRecord(module=module, **options))


def manifest(directory):
    return host.HostManifest(data_dir=directory / "data")


def test_storage_migrates_version_one_records(tmp_path):
    with PluginStorage(tmp_path) as storage:
        storage.save(PluginRecord(module="sample"))
    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        connection.execute(
            "UPDATE plugins SET record=?", (json.dumps({"module": "sample"}),)
        )
        connection.execute("PRAGMA user_version=1")
    with PluginStorage(tmp_path) as storage:
        assert storage.get("sample").protected is False
    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_storage_prevents_generic_protection_change_and_delete(tmp_path):
    record = PluginRecord(
        module="sample",
        enabled=True,
        autoload=True,
        required=True,
        protected=True,
    )
    with PluginStorage(tmp_path) as storage:
        storage.save(record)
        with pytest.raises(PluginProtectedError):
            storage.save(record.model_copy(update={"protected": False}))
        with pytest.raises(PluginProtectedError):
            storage.delete("sample")


@pytest.mark.anyio
async def test_protected_plugin_management_preserves_state(host_dir):
    register(
        host_dir,
        "host_test_core",
        "",
        enabled=True,
        autoload=True,
        required=True,
        protected=True,
    )
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            plugin = get_plugin("host_test_core")
            manager = host.get_host()
            with pytest.raises(PluginProtectedError):
                await manager.disable(plugin.module_name)
            with pytest.raises(PluginProtectedError):
                await manager.apply_config(plugin.module_name)
            with pytest.raises(PluginProtectedError):
                await manager.managers[plugin.module_name].stop_plugin(
                    plugin.module_name
                )
            with pytest.raises(PluginProtectedError):
                registry.revoke_plugin(plugin)
            assert (
                plugin.ready and manager.storage.get(plugin.module_name).enabled
            )
        assert plugin.cleanup_complete and plugin.status is PluginStatus.STOPPED


@pytest.mark.anyio
async def test_running_host_excludes_offline_management(host_dir):
    async with host.load_host(manifest(host_dir)):
        with pytest.raises(OSError):
            with host._data_lock(host_dir / "data"):
                pytest.fail("A second host obtained the same state directory")


@pytest.mark.anyio
async def test_shutdown_cancels_host_management_startup(host_dir):
    import anyio

    register(
        host_dir,
        "host_test_pending_start",
        "import asyncio\n"
        "from opsmesh import on_startup, on_shutdown\n"
        "entered = asyncio.Event()\n"
        "closed = []\n"
        "@on_startup\n"
        "async def startup():\n"
        "    entered.set()\n"
        "    await asyncio.Event().wait()\n"
        "@on_shutdown\n"
        "def shutdown():\n"
        "    closed.append(True)\n",
        enabled=True,
    )
    async with host.load_host(manifest(host_dir)) as driver:
        manager = host.get_host()
        async with driver.lifespan():
            plugin = manager.load("host_test_pending_start")
            driver._lifespan.shutdown_timeout = 0.1
            pending = asyncio.create_task(manager.enable(plugin.module_name))
            await plugin.module.entered.wait()
        with anyio.fail_after(1):
            with pytest.raises(RuntimeError, match="cancelled during shutdown"):
                await pending
        assert plugin.cleanup_complete and plugin.module.closed == [True]


@pytest.mark.parametrize("field", ["plugins", "plugin_paths", "http"])
def test_host_rejects_plugin_and_transport_settings(field):
    with pytest.raises(ValidationError, match="Extra inputs"):
        host.HostManifest.model_validate({field: []})


def test_manifest_resolves_only_the_host_data_directory(host_dir):
    path = host_dir / "host.json"
    path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
    assert host.load_manifest(path).data_dir == host_dir / "state"


@pytest.mark.anyio
async def test_disabled_plugin_is_not_automatically_imported(host_dir):
    register(host_dir, "host_test_disabled", "raise AssertionError('imported')")
    async with host.load_host(manifest(host_dir)):
        assert "host_test_disabled" not in sys.modules


@pytest.mark.anyio
async def test_dependency_uses_its_record_and_cannot_enable_itself(host_dir):
    register(
        host_dir, "host_test_dependency", "raise AssertionError('imported')"
    )
    register(
        host_dir,
        "host_test_root",
        "from opsmesh import require\nrequire('host_test_dependency')\n",
        enabled=True,
        autoload=True,
        required=True,
    )
    with pytest.raises(RuntimeError, match="Cannot load plugin"):
        async with host.load_host(manifest(host_dir)):
            pass
    assert "host_test_dependency" not in sys.modules
    assert not driver_state.is_initialized()
    assert registry._managers == []


@pytest.mark.anyio
async def test_dependency_is_configured_before_root_import(host_dir):
    register(
        host_dir,
        "host_test_dependency",
        "from pydantic import BaseModel\n"
        "from opsmesh import get_plugin_config\n"
        "class Settings(BaseModel):\n    value: str\n"
        "settings = get_plugin_config(Settings)\n",
        enabled=True,
        config={"value": "stored"},
    )
    register(
        host_dir,
        "host_test_root",
        "from opsmesh import require\n"
        "dependency = require('host_test_dependency')\n",
        enabled=True,
        autoload=True,
    )
    async with host.load_host(manifest(host_dir)):
        plugin = get_plugin("host_test_root")
        assert plugin.module.dependency.settings.value == "stored"
        assert plugin.dependencies == ["host_test_dependency"]


CONFIGURED_PLUGIN = """
from pydantic import BaseModel
from opsmesh import get_plugin_config, on_startup, on_shutdown
from opsmesh.plugins import PluginMetadata
class Settings(BaseModel):
    value: str
__plugin_meta__ = PluginMetadata(
    name="Example", description="Configuration", usage="Test", config=Settings
)
events = []
@on_startup
async def startup():
    events.append(get_plugin_config(Settings).value)
@on_shutdown
async def shutdown():
    events.append("closed")
"""


@pytest.mark.anyio
async def test_save_does_not_apply_or_restart_and_apply_uses_saved_values(
    host_dir,
):
    register(
        host_dir,
        "host_test_configured",
        CONFIGURED_PLUGIN,
        config={"value": "old"},
        enabled=True,
        autoload=True,
    )
    async with host.load_host(manifest(host_dir)) as driver:
        plugin = get_plugin("host_test_configured")
        async with driver.lifespan():
            await host.get_host().save_config(
                plugin.module_name, {"value": "new"}
            )
            assert plugin.module.events == ["old"]
            assert plugin.config_values == {"value": "old"}
            await host.get_host().apply_config(plugin.module_name)
            assert plugin.module.events == ["old", "closed", "new"]
            assert plugin.ready
        assert plugin.cleanup_complete
    with PluginStorage(host_dir / "data") as storage:
        assert storage.get("host_test_configured").config == {"value": "new"}


@pytest.mark.anyio
async def test_disabled_configuration_loads_declarations_without_resources(
    host_dir,
):
    register(host_dir, "host_test_configured", CONFIGURED_PLUGIN)
    async with host.load_host(manifest(host_dir)):
        manager = host.get_host()
        await manager.save_config("host_test_configured", {"value": "saved"})
        plugin = get_plugin("host_test_configured")
        assert plugin.module.events == []
        assert not manager.records[plugin.module_name].enabled


@pytest.mark.anyio
async def test_enable_and_disable_are_persisted_without_unloading_code(
    host_dir,
):
    register(
        host_dir,
        "host_test_configured",
        CONFIGURED_PLUGIN,
        config={"value": "ready"},
    )
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            manager = host.get_host()
            await manager.enable("host_test_configured")
            plugin = get_plugin("host_test_configured")
            assert plugin.ready
            await manager.disable(plugin.module_name)
            assert plugin.status is PluginStatus.STOPPED
            assert plugin.module_name in sys.modules
            assert not manager.storage.get(plugin.module_name).enabled
            await manager.enable(plugin.module_name)
            assert plugin.module.events == ["ready", "closed", "ready"]


@pytest.mark.anyio
async def test_registration_does_not_install_or_execute_files(host_dir):
    source = host_dir / "host_test_manual.py"
    source.write_text("raise AssertionError('imported')", encoding="utf-8")
    async with host.load_host(manifest(host_dir), load_entries=False):
        manager = host.get_host()
        await manager.register(
            PluginRecord(module="host_test_manual", directory=str(host_dir))
        )
        assert "host_test_manual" not in sys.modules
        assert source.read_text() == "raise AssertionError('imported')"


@pytest.mark.anyio
async def test_host_lock_prevents_concurrent_management(host_dir):
    async with host.load_host(manifest(host_dir)):
        with pytest.raises(OSError):
            with host._data_lock(host_dir / "data"):
                pass


def test_storage_survives_reopen_and_does_not_accept_package_fields(host_dir):
    record = PluginRecord(module="host_test_record", config={"value": [1, 2]})
    with PluginStorage(host_dir / "data") as storage:
        storage.save(record)
    with PluginStorage(host_dir / "data") as storage:
        assert storage.get(record.module) == record
        storage.delete(record.module)
        assert storage.list() == []
    with pytest.raises(ValidationError):
        PluginRecord(module="valid", checksum="installer responsibility")


@pytest.mark.anyio
async def test_online_enable_uses_values_saved_while_disabled(host_dir):
    register(host_dir, "host_test_configured", CONFIGURED_PLUGIN)
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            manager = host.get_host()
            await manager.save_config("host_test_configured", {"value": "new"})
            await manager.enable("host_test_configured")
            assert get_plugin("host_test_configured").module.events == ["new"]


@pytest.mark.anyio
async def test_save_failure_preserves_active_and_persisted_config(
    host_dir, monkeypatch
):
    register(
        host_dir,
        "host_test_configured",
        CONFIGURED_PLUGIN,
        config={"value": "old"},
        enabled=True,
        autoload=True,
    )
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            manager = host.get_host()

            def fail(record):
                raise OSError("disk full")

            monkeypatch.setattr(manager.storage, "save", fail)
            with pytest.raises(OSError, match="disk full"):
                await manager.save_config(
                    "host_test_configured", {"value": "new"}
                )
            plugin = get_plugin("host_test_configured")
            assert plugin.ready
            assert plugin.module.events == ["old"]
            assert manager.storage.get(plugin.module_name).config == {
                "value": "old"
            }
