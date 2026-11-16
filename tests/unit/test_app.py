"""Check persisted plugin configuration and host ownership boundaries."""

import asyncio
import json
import sqlite3
import subprocess
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
from opsmesh.plugins.storage import SCHEMA_VERSION, PluginRecord, PluginStorage


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


DEPENDENCY_PROVIDER = """
from pydantic import BaseModel, ConfigDict
from opsmesh import get_dependency_config
from opsmesh.drivers import get_driver
from opsmesh.plugins import PluginMetadata, on_plugin_loaded

class ConsumerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accounts: list[str]

__plugin_meta__ = PluginMetadata(
    "provider", "test provider", "test", dependency_config=ConsumerSettings
)
observed = []
@on_plugin_loaded
def configure(consumer):
    if "host_test_provider" not in consumer.dependencies:
        return
    def startup():
        observed.append((consumer.name, get_dependency_config(ConsumerSettings)))
    get_driver().on_startup(startup)
"""


@pytest.mark.anyio
async def test_dependency_configuration_is_isolated_persisted_and_reapplied(
    host_dir,
):
    register(host_dir, "host_test_provider", DEPENDENCY_PROVIDER, enabled=True)
    for name, account in (
        ("host_test_first", "first"),
        ("host_test_second", "second"),
    ):
        register(
            host_dir,
            name,
            'from opsmesh import require\nprovider = require("host_test_provider")\n',
            enabled=True,
            autoload=True,
            dependencies={"host_test_provider": {"accounts": [account]}},
        )
    async with host.load_host(manifest(host_dir)) as driver:
        manager = host.get_host()
        first = get_plugin("host_test_first")
        provider = get_plugin("host_test_provider")
        async with driver.lifespan():
            observed = dict(provider.module.observed)
            assert observed["host_test_first"].accounts == ["first"]
            assert observed["host_test_second"].accounts == ["second"]
            observed["host_test_first"].accounts.append("local mutation")
            assert first.dependency_config_values["host_test_provider"] == {
                "accounts": ["first"]
            }
            updated = {"host_test_provider": {"accounts": ["changed"]}}
            await manager.save_config(
                first.module_name,
                {},
                dependencies=updated,
            )
            updated["host_test_provider"]["accounts"].append("caller mutation")
            assert manager.records[first.module_name].dependencies == {
                "host_test_provider": {"accounts": ["changed"]}
            }
            assert first.dependency_config_values["host_test_provider"][
                "accounts"
            ] == ["first"]
            await manager.apply_config(first.module_name)
            assert provider.module.observed[-1][1].accounts == ["changed"]
            with pytest.raises(ValueError, match="accounts"):
                await manager.save_config(
                    first.module_name,
                    {},
                    dependencies={"host_test_provider": {}},
                )
            assert manager.records[first.module_name].dependencies[
                "host_test_provider"
            ] == {"accounts": ["changed"]}
    with PluginStorage(host_dir / "data") as storage:
        assert storage.get("host_test_first").dependencies == {
            "host_test_provider": {"accounts": ["changed"]}
        }


@pytest.mark.anyio
async def test_invalid_dependency_configuration_can_be_repaired(host_dir):
    register(host_dir, "host_test_provider", DEPENDENCY_PROVIDER, enabled=True)
    register(
        host_dir,
        "host_test_repairable",
        'from opsmesh import require\nrequire("host_test_provider")\n',
        enabled=False,
        dependencies={"host_test_provider": {}},
    )
    async with host.load_host(manifest(host_dir)) as driver:
        manager = host.get_host()
        plugin = manager.load("host_test_repairable")
        assert not plugin.ready
        await manager.save_config(
            plugin.module_name,
            {},
            dependencies={"host_test_provider": {"accounts": ["repaired"]}},
        )
        async with driver.lifespan():
            await manager.enable(plugin.module_name)
            assert plugin.ready
            provider = get_plugin("host_test_provider")
            assert provider.module.observed[-1][1].accounts == ["repaired"]


def test_cli_imports_configuration_and_dependencies_from_one_file(host_dir):
    from opsmesh.cli import main

    source = host_dir / "host_test_configured.py"
    source.write_text("value = 1\n", encoding="utf-8")
    host_file = host_dir / "host.json"
    host_file.write_text(
        manifest(host_dir).model_dump_json(), encoding="utf-8"
    )
    values = {
        "config": {"value": "saved"},
        "dependencies": {"provider": {"endpoint": "test"}},
    }
    config_file = host_dir / "plugin.json"
    config_file.write_text(json.dumps(values), encoding="utf-8")
    assert (
        main(
            [
                "--manifest",
                str(host_file),
                "--plugin-action",
                "register",
                "--module",
                "host_test_configured",
                "--directory",
                str(host_dir),
                "--config",
                str(config_file),
            ]
        )
        == 0
    )
    with PluginStorage(host_dir / "data") as storage:
        record = storage.get("host_test_configured")
        assert record.config == values["config"]
        assert record.dependencies == values["dependencies"]


FILE_CONFIG_PLUGIN = """
from pydantic import BaseModel
from opsmesh import get_plugin_config, on_startup
from opsmesh.plugins import PluginMetadata

class Settings(BaseModel):
    label: str = "default"

__plugin_meta__ = PluginMetadata(
    "File settings", "File settings test", "test",
    config=Settings, config_file="settings.json",
)
observed = []
@on_startup
def start():
    observed.append(get_plugin_config(Settings).label)
"""


@pytest.mark.anyio
async def test_declared_file_initializes_once_and_can_be_reimported(host_dir):
    name = "host_test_file"
    register(host_dir, name, FILE_CONFIG_PLUGIN, enabled=True, autoload=True)
    settings = host_dir / "settings.json"
    settings.write_text('{"config": {"label": "initial"}}', encoding="utf-8")
    async with host.load_host(manifest(host_dir)) as driver:
        manager = host.get_host()
        plugin = get_plugin(name)
        assert not manager.records[name].config_initialized
        async with driver.lifespan():
            assert plugin.module.observed == ["initial"]
            assert manager.records[name].config_initialized
            settings.write_text(
                '{"config": {"label": "edited"}}', encoding="utf-8"
            )
            await manager.disable(name)
            await manager.enable(name)
            assert plugin.module.observed[-1] == "initial"
            await manager.import_config(name)
            assert plugin.config_values == {"label": "initial"}
            await manager.apply_config(name)
            assert plugin.module.observed[-1] == "edited"
            settings.write_text("invalid-json", encoding="utf-8")
            with pytest.raises(ValidationError):
                await manager.import_config(name)
            assert manager.records[name].config == {"label": "edited"}
    with PluginStorage(host_dir / "data") as storage:
        assert storage.get(name).config == {"label": "edited"}
    settings.unlink()
    restart = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
import sys
from pathlib import Path
from opsmesh import app
from opsmesh.plugins import get_plugin
sys.path.insert(0, sys.argv[1])
app.entry_points = lambda **kwargs: ()
async def main():
    manifest = app.HostManifest(data_dir=Path(sys.argv[1]) / "data")
    async with app.load_host(manifest) as driver:
        async with driver.lifespan():
            assert get_plugin("host_test_file").module.observed == ["edited"]
asyncio.run(main())
""",
            str(host_dir),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert restart.returncode == 0, restart.stdout + restart.stderr


@pytest.mark.anyio
async def test_explicit_empty_configuration_skips_missing_file(host_dir):
    name = "host_test_empty"
    register(
        host_dir,
        name,
        FILE_CONFIG_PLUGIN,
        enabled=True,
        autoload=True,
        config={},
    )
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            plugin = get_plugin(name)
            assert plugin.ready
            assert plugin.module.observed == ["default"]


@pytest.mark.anyio
async def test_required_dependency_uses_its_own_config_file(host_dir):
    register(host_dir, "host_test_provider", DEPENDENCY_PROVIDER, enabled=True)
    source = (
        FILE_CONFIG_PLUGIN
        + """
from opsmesh import require
require("host_test_provider")
"""
    )
    register(host_dir, "host_test_child", source, enabled=True)
    register(
        host_dir,
        "host_test_parent",
        'from opsmesh import require\nrequire("host_test_child")\n',
        enabled=True,
        autoload=True,
    )
    (host_dir / "settings.json").write_text(
        json.dumps(
            {
                "config": {"label": "child"},
                "dependencies": {
                    "host_test_provider": {"accounts": ["robot"]}
                },
            }
        ),
        encoding="utf-8",
    )
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            assert get_plugin("host_test_parent").ready
            assert get_plugin("host_test_child").module.observed == ["child"]
            provider = get_plugin("host_test_provider")
            assert provider.module.observed[-1][1].accounts == ["robot"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        None,
        "invalid-json",
        '{"config":{"label":12}}',
        '{"dependencies":{"undeclared":{}}}',
    ],
)
async def test_invalid_file_is_repairable_without_partial_save(
    host_dir, content
):
    name = "host_test_invalid_file"
    register(host_dir, name, FILE_CONFIG_PLUGIN, enabled=True, autoload=True)
    settings = host_dir / "settings.json"
    if content is not None:
        settings.write_text(content, encoding="utf-8")
    async with host.load_host(manifest(host_dir)) as driver:
        manager = host.get_host()
        async with driver.lifespan():
            plugin = get_plugin(name)
            assert plugin.status is PluginStatus.FAILED
            assert not manager.records[name].config_initialized
            assert not manager.storage.get(name).config_initialized
            await manager.save_config(name, {"label": "repaired"})
            await manager.enable(name)
            assert plugin.ready
            assert plugin.module.observed == ["repaired"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "filename",
    ["../outside.json", "/outside.json", "C:/outside.json", "C:outside.json"],
)
async def test_config_file_rejects_paths_outside_plugin(host_dir, filename):
    name = "host_test_unsafe_file"
    source = FILE_CONFIG_PLUGIN.replace('"settings.json"', repr(filename))
    register(host_dir, name, source)
    async with host.load_host(manifest(host_dir)):
        with pytest.raises(ValueError, match="within the plugin package"):
            await host.get_host().import_config(name)


def test_cli_check_imports_file_without_starting_resources(host_dir):
    from opsmesh.cli import main

    source = FILE_CONFIG_PLUGIN.replace(
        "observed.append(get_plugin_config(Settings).label)",
        'raise AssertionError("Must not start resources")',
    )
    register(
        host_dir, "host_test_check_file", source, enabled=True, autoload=True
    )
    settings = host_dir / "settings.json"
    settings.write_text('{"config":{"label":"checked"}}', encoding="utf-8")
    host_file = host_dir / "host.json"
    host_file.write_text(
        manifest(host_dir).model_dump_json(), encoding="utf-8"
    )
    assert main(["--manifest", str(host_file), "--check"]) == 0
    with PluginStorage(host_dir / "data") as storage:
        assert storage.get("host_test_check_file").config == {
            "label": "checked"
        }


@pytest.mark.anyio
async def test_package_configuration_is_relative_to_entry_package(host_dir):
    name = "host_test_package_file"
    package = host_dir / name
    package.mkdir()
    (package / "assets").mkdir()
    (package / "__init__.py").write_text(
        FILE_CONFIG_PLUGIN.replace('"settings.json"', '"assets/options.json"'),
        encoding="utf-8",
    )
    (package / "assets" / "options.json").write_text(
        '{"config":{"label":"package"}}', encoding="utf-8"
    )
    with PluginStorage(host_dir / "data") as storage:
        storage.save(PluginRecord(module=name, enabled=True, autoload=True))
    async with host.load_host(manifest(host_dir)) as driver:
        async with driver.lifespan():
            assert get_plugin(name).module.observed == ["package"]


@pytest.mark.anyio
async def test_configuration_rejects_symlink_to_outside_plugin(host_dir):
    name = "host_test_symlink_file"
    package = host_dir / name
    package.mkdir()
    (package / "__init__.py").write_text(FILE_CONFIG_PLUGIN, encoding="utf-8")
    outside = host_dir / "outside.json"
    outside.write_text('{"config":{"label":"outside"}}', encoding="utf-8")
    try:
        (package / "settings.json").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable")
    with PluginStorage(host_dir / "data") as storage:
        storage.save(PluginRecord(module=name))
    async with host.load_host(manifest(host_dir)):
        with pytest.raises(ValueError, match="outside the plugin package"):
            await host.get_host().import_config(name)


def test_cli_import_config_uses_declared_file(host_dir):
    from opsmesh.cli import main

    name = "host_test_cli_file"
    register(host_dir, name, FILE_CONFIG_PLUGIN, config={"label": "old"})
    (host_dir / "settings.json").write_text(
        '{"config":{"label":"new"}}', encoding="utf-8"
    )
    host_file = host_dir / "host.json"
    host_file.write_text(
        manifest(host_dir).model_dump_json(), encoding="utf-8"
    )
    assert (
        main(
            [
                "--manifest",
                str(host_file),
                "--plugin-action",
                "import-config",
                "--module",
                name,
            ]
        )
        == 0
    )
    with PluginStorage(host_dir / "data") as storage:
        assert storage.get(name).config == {"label": "new"}


@pytest.mark.anyio
@pytest.mark.parametrize("register_online", [False, True])
async def test_external_plugin_namespace_is_discovered_and_restored(
    host_dir, register_online
):
    import opsmesh
    import opsmesh.plugins

    original_root = list(opsmesh.__path__)
    original_plugins = list(opsmesh.plugins.__path__)
    external = host_dir / "external"
    package = external / "opsmesh" / "plugins" / "host_test_external"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("value = 42\n", encoding="utf-8")
    record = PluginRecord(
        module="opsmesh.plugins.host_test_external",
        directory=str(external),
        enabled=True,
        autoload=True,
        required=True,
    )
    if not register_online:
        with PluginStorage(host_dir / "data") as storage:
            storage.save(record)
    try:
        async with host.load_host(manifest(host_dir)):
            manager = host.get_host()
            if register_online:
                await manager.register(record)
            plugin = manager.load(record.module)
            assert plugin.module.value == 42
            assert plugin.module.__file__ == str(package / "__init__.py")
        assert list(opsmesh.__path__) == original_root
        assert list(opsmesh.plugins.__path__) == original_plugins
    finally:
        sys.modules.pop(record.module, None)
        vars(opsmesh.plugins).pop("host_test_external", None)


@pytest.mark.parametrize("old_version", [1, 2])
def test_storage_migrates_existing_records(tmp_path, old_version):
    with PluginStorage(tmp_path) as storage:
        storage.save(PluginRecord(module="sample"))
    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        connection.execute(
            "UPDATE plugins SET record=?", (json.dumps({"module": "sample"}),)
        )
        connection.execute(f"PRAGMA user_version={old_version}")
    with PluginStorage(tmp_path) as storage:
        assert storage.get("sample").protected is False
        assert storage.get("sample").config_initialized is True
    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )


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
                plugin.ready
                and manager.storage.get(plugin.module_name).enabled
            )
        assert (
            plugin.cleanup_complete and plugin.status is PluginStatus.STOPPED
        )


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
            with pytest.raises(
                RuntimeError, match="cancelled during shutdown"
            ):
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
    register(
        host_dir, "host_test_disabled", "raise AssertionError('imported')"
    )
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
