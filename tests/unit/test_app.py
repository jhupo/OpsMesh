"""Check persisted plugin configuration and host ownership boundaries."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from importlib.metadata import entry_points

import anyio
import pytest
from pydantic import ValidationError

from opsmesh.plugins import get_available_plugin_names, get_plugin, registry
from opsmesh.plugins.model import PluginStatus
from opsmesh.service import host, service
from opsmesh.service.catalog import (
    SCHEMA_VERSION,
    PluginProtectedError,
    PluginRecord,
    PluginStorage,
)
from opsmesh.service.control import _dispatch, send_control, serve_control


@pytest.fixture
def host_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_plugins", {})
    monkeypatch.setattr(
        registry, "_module_plugins",
        {plugin.module_name: plugin for plugin in registry._plugins.values()},
    )
    monkeypatch.setattr(host, "entry_points", lambda **kwargs: ())
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in tuple(sys.modules):
        if name.startswith("host_test_"):
            del sys.modules[name]


def register(directory, module, source, **options):
    (directory / f"{module}.py").write_text(source, encoding="utf-8")
    with PluginStorage(directory / "data") as storage:
        storage.save(PluginRecord(module=module, **options))


def manifest(directory):
    return host.HostManifest(data_dir=directory / "data")


@pytest.mark.anyio
async def test_online_stop_and_recovery_preserve_builtin_provider(
    host_dir, monkeypatch
):
    provider = "host_test_fixed_provider"
    extension = "host_test_extension"
    consumer = "host_test_consumer"
    register(host_dir, provider, "", enabled=True, autoload=True)
    register(
        host_dir,
        extension,
        f"from opsmesh import require\nrequire({provider!r})",
        enabled=True,
        autoload=True,
    )
    register(
        host_dir,
        consumer,
        f"from opsmesh import require\nrequire({extension!r})",
        enabled=True,
    )
    metadata = host_dir / "test_provider-1.0.dist-info"
    metadata.mkdir()
    (metadata / "entry_points.txt").write_text(
        f"[opsmesh.builtin_plugins]\n{provider} = {provider}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        host,
        "entry_points",
        lambda *, group: entry_points(group=group, name=provider),
    )
    async with (
        host.load_host(manifest(host_dir)) as manager,
        manager.driver.lifespan(),
    ):

        async def request(module, action):
            payload = {"version": 1, "module": module, "action": action}
            return await _dispatch(manager, json.dumps(payload).encode())

        fixed = get_plugin(provider)
        assert manager.plugin_status(fixed.module_name)["protected"]
        assert fixed.ready
        assert not manager.records[provider].protected
        assert (await request(provider, "status"))["result"]["builtin"]
        assert "not loaded" in (await request(consumer, "stop"))["error"]
        initial = (await request(extension, "status"))["result"]
        stopped = (await request(extension, "stop"))["result"]
        assert stopped["state"] == "stopped"
        assert stopped["enabled"] and stopped["manually_stopped"]
        assert stopped["cleanup_complete"]
        rejection = await request(consumer, "start")
        assert "manually stopped dependency" in rejection["error"]
        for action in ("load", "stop", "start", "restart"):
            assert "error" in await request(provider, action)
        with pytest.raises(PluginProtectedError):
            await manager.register(PluginRecord(module=f"{provider}.reserved"))
        resumed = (await request(extension, "start"))["result"]
        assert resumed["state"] == "ready"
        assert not resumed["manually_stopped"]
        assert resumed["pid"] == initial["pid"]
        assert resumed["generation"] == initial["generation"] + 1
        assert fixed.ready and fixed.generation == 1


@pytest.mark.anyio
async def test_online_start_cannot_activate_a_failed_builtin_dependency(
    host_dir, monkeypatch
):
    from importlib.metadata import EntryPoint

    provider = "host_test_fixed_dependency"
    consumer = "host_test_fixed_consumer"
    register(
        host_dir,
        provider,
        "from opsmesh import on_startup\n"
        "@on_startup\n"
        "def startup():\n"
        "    raise RuntimeError('initial activation failed')\n",
        enabled=True,
        autoload=True,
    )
    register(
        host_dir,
        consumer,
        f"from opsmesh import require\nrequire({provider!r})\n",
        enabled=True,
    )
    monkeypatch.setattr(
        host,
        "entry_points",
        lambda *, group: (EntryPoint(provider, provider, group),)
        if group == "opsmesh.builtin_plugins"
        else (),
    )
    async with host.load_host(manifest(host_dir)) as current:
        async with current.driver.lifespan():
            plugin = get_plugin(provider)
            assert plugin.status is PluginStatus.FAILED
            with pytest.raises(PluginProtectedError, match=provider):
                await current.control_plugin(consumer, "start")
            assert plugin.generation == 1
            assert get_plugin(consumer).generation == 0


@pytest.mark.skipif(os.name != "posix", reason="Linux local control socket")
@pytest.mark.anyio
@pytest.mark.parametrize("custom_runtime", [False, True])
async def test_online_control_socket_uses_the_running_host(
    host_dir, custom_runtime
):
    module = "host_test_local_control"
    register(host_dir, module, "", enabled=True, autoload=True)
    async with (
        host.load_host(manifest(host_dir)) as current,
        current.driver.lifespan(),
    ):
        runtime_dir = host_dir / "run" if custom_runtime else None
        async with serve_control(current, runtime_dir=runtime_dir):
            root = current.storage.root
            socket = (runtime_dir or root / "control") / "host.sock"
            assert socket.is_socket()
            with pytest.raises(BlockingIOError):
                async with serve_control(current, runtime_dir=runtime_dir):
                    pytest.fail(
                        "A second listener acquired the runtime directory"
                    )
            stopped = (
                await send_control(
                    root, "stop", module, runtime_dir=runtime_dir
                )
            )["result"]
            resumed = (
                await send_control(
                    root, "start", module, runtime_dir=runtime_dir
                )
            )["result"]
            assert stopped["state"] == "stopped"
            assert resumed["state"] == "ready"
            assert stopped["pid"] == resumed["pid"] == os.getpid()
        assert not socket.exists()


def test_manifest_resolves_runtime_directory(host_dir):
    path = host_dir / "host.json"
    path.write_text('{"data_dir":"./state","runtime_dir":"./run"}')
    settings = host.load_manifest(path)
    assert settings.data_dir == host_dir / "state"
    assert settings.runtime_dir == host_dir / "run"


@pytest.mark.anyio
async def test_service_never_reports_ready_when_local_control_fails_to_bind(
    host_dir, monkeypatch
):
    module = "host_test_service"
    register(host_dir, module, "", enabled=True, autoload=True)
    notifications = []

    @asynccontextmanager
    async def unavailable_control(current, *, runtime_dir):
        assert current.driver.started
        raise OSError("cannot bind control socket")
        yield

    monkeypatch.setattr(service, "serve_control", unavailable_control)
    monkeypatch.setattr(service, "_notify", notifications.append)
    settings = manifest(host_dir)
    with pytest.raises(OSError, match="cannot bind control socket"):
        await service.serve(settings)
    assert notifications == []
    assert get_plugin(module) is None
    with host._data_lock(settings.data_dir):
        pass
    async with host.load_host(settings):
        pass


@pytest.mark.anyio
async def test_driver_construction_failure_releases_host_ownership(
    host_dir, monkeypatch
):
    def fail_driver(*args):
        raise RuntimeError("driver construction failed")

    settings = manifest(host_dir)
    with monkeypatch.context() as patch:
        patch.setattr(host, "Driver", fail_driver)
        with pytest.raises(RuntimeError, match="driver construction failed"):
            async with host.load_host(settings):
                pytest.fail("Opened a host with a failed driver")
    with host._data_lock(settings.data_dir):
        pass
    async with host.load_host(settings) as current:
        assert isinstance(current.driver, host.Driver)


@pytest.mark.anyio
async def test_host_reserves_process_before_opening_data(host_dir, monkeypatch):
    acquiring = anyio.Event()
    proceed = anyio.Event()
    open_sync = host.run_sync_ctx_manager

    @asynccontextmanager
    async def pause_first_open(context):
        if not acquiring.is_set():
            acquiring.set()
            await proceed.wait()
        async with open_sync(context) as value:
            yield value

    async def open_first():
        async with host.load_host(manifest(host_dir)):
            pass

    monkeypatch.setattr(host, "run_sync_ctx_manager", pause_first_open)
    other = host.HostManifest(data_dir=host_dir / "other")
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(open_first)
        await acquiring.wait()
        with pytest.raises(RuntimeError, match="already owns this process"):
            async with host.load_host(other):
                pytest.fail("Second host obtained process ownership")
        assert not other.data_dir.exists()
        proceed.set()


DEPENDENCY_PROVIDER = """
from pydantic import BaseModel, ConfigDict
from opsmesh import get_dependency_config, on_startup
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
    @on_startup(before=True)
    def startup():
        settings = get_dependency_config(ConsumerSettings)
        observed.append((consumer.name, settings))
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
            'from opsmesh import require\n'
            'provider = require("host_test_provider")\n',
            enabled=True,
            autoload=True,
            dependencies={"host_test_provider": {"accounts": [account]}},
        )
    async with host.load_host(manifest(host_dir)) as manager:
        first = get_plugin("host_test_first")
        provider = get_plugin("host_test_provider")
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        plugin = manager.load("host_test_repairable")
        assert not plugin.ready
        await manager.save_config(
            plugin.module_name,
            {},
            dependencies={"host_test_provider": {"accounts": ["repaired"]}},
        )
        async with manager.driver.lifespan():
            await manager.enable(plugin.module_name)
            assert plugin.ready
            provider = get_plugin("host_test_provider")
            assert provider.module.observed[-1][1].accounts == ["repaired"]


def test_cli_imports_configuration_and_dependencies_from_one_file(host_dir):
    from opsmesh.service.cli import main

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
    async with host.load_host(manifest(host_dir)) as manager:
        plugin = get_plugin(name)
        assert not manager.records[name].config_initialized
        async with manager.driver.lifespan():
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
from opsmesh.service import host as app
from opsmesh.plugins import get_available_plugin_names, get_plugin
sys.path.insert(0, sys.argv[1])
app.entry_points = lambda **kwargs: ()
async def main():
    manifest = app.HostManifest(data_dir=Path(sys.argv[1]) / "data")
    async with app.load_host(manifest) as manager:
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        with pytest.raises(ValueError, match="within the plugin package"):
            await manager.import_config(name)


def test_cli_check_imports_file_without_starting_resources(host_dir):
    from opsmesh.service.cli import main

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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        with pytest.raises(ValueError, match="outside the plugin package"):
            await manager.import_config(name)


def test_cli_import_config_uses_declared_file(host_dir):
    from opsmesh.service.cli import main

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
        async with host.load_host(manifest(host_dir)) as manager:
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
            plugin = get_plugin("host_test_core")
            with pytest.raises(PluginProtectedError):
                await manager.disable(plugin.module_name)
            with pytest.raises(PluginProtectedError):
                await manager.apply_config(plugin.module_name)
            with pytest.raises(PluginProtectedError):
                await manager.control_plugin(plugin.module_name, "stop")
            with pytest.raises(PluginProtectedError):
                await manager.control_plugin(plugin.module_name, "restart")
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
            plugin = manager.load("host_test_pending_start")
            manager.driver._lifespan.shutdown_timeout = 0.1
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
    assert not get_available_plugin_names()


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
    async with host.load_host(manifest(host_dir)) as manager:
        plugin = get_plugin("host_test_configured")
        async with manager.driver.lifespan():
            await manager.save_config(
                plugin.module_name, {"value": "new"}
            )
            assert plugin.module.events == ["old"]
            assert plugin.config_values == {"value": "old"}
            await manager.apply_config(plugin.module_name)
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
    async with host.load_host(manifest(host_dir)) as manager:
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
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
    settings = manifest(host_dir)
    async with host.load_host(settings, load_entries=False) as manager:
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():
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
    async with host.load_host(manifest(host_dir)) as manager:
        async with manager.driver.lifespan():

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
