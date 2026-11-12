"""Check plugin ownership resolution and filesystem lifecycle integration."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

import opsmesh.plugins.filesystem as filesystem
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.plugins import load as plugin_load
from opsmesh.plugins import manager as plugin_manager
from opsmesh.plugins import registry
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.filesystem import files
from opsmesh.plugins.filesystem._paths import resolve_caller_plugin


@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(registry, "_plugins", {})
    managers: list[PluginManager] = []
    monkeypatch.setattr(registry, "_managers", managers)
    monkeypatch.setattr(plugin_manager, "_managers", managers)
    monkeypatch.setattr(plugin_load, "_managers", managers)
    yield managers
    for name, module in list(sys.modules.items()):
        if name.startswith("filesystem_owner_") and isinstance(
            module, ModuleType
        ):
            del sys.modules[name]


def _load_owner(tmp_path, monkeypatch, managers):
    monkeypatch.syspath_prepend(str(tmp_path))
    name = "filesystem_owner_demo"
    (tmp_path / f"{name}.py").write_text(
        """
from opsmesh.plugins.filesystem._paths import resolve_caller_plugin


def owner():
    return resolve_caller_plugin().module_name
""",
        encoding="utf-8",
    )
    manager = PluginManager([name])
    managers.append(manager)
    return manager.load_plugin(name)


def test_caller_plugin_is_resolved_from_its_module(
    tmp_path, monkeypatch, clean_registry
):
    plugin = _load_owner(tmp_path, monkeypatch, clean_registry)
    assert plugin is not None
    module = sys.modules["filesystem_owner_demo"]
    assert module.owner() == "filesystem_owner_demo"


def test_unregistered_caller_is_rejected(clean_registry):
    with pytest.raises(filesystem.FilesystemError, match="calling plugin"):
        resolve_caller_plugin()


@pytest.mark.anyio
async def test_lifecycle_prepares_roots_and_rejects_access(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(driver_state, "_driver", None)
    driver = Driver(Env(), Config())
    monkeypatch.setattr(
        files,
        "get_plugin_config",
        lambda model, **kwargs: model(root=str(tmp_path / "files")),
    )
    driver_state.set_driver(driver)
    plugin = filesystem
    driver.on_startup(files._startup)
    driver.on_shutdown(files._shutdown)
    monkeypatch.setattr(
        files,
        "resolve_caller_plugin",
        lambda: SimpleNamespace(module_name="demo_plugin"),
    )

    with pytest.raises(plugin.FilesystemClosed):
        plugin.data_dir()

    async with driver.lifespan():
        root = tmp_path / "files"
        assert (root / "data").is_dir()
        assert (root / "cache").is_dir()
        assert (root / "config").is_dir()
        assert plugin.data_dir() == root / "data" / "demo_plugin"
        assert plugin.data_file("notes/today.txt") == (
            root / "data" / "demo_plugin" / "notes" / "today.txt"
        )
        assert plugin.cache_file("response.json") == (
            root / "cache" / "demo_plugin" / "response.json"
        )
        assert plugin.config_file("preferences.toml") == (
            root / "config" / "demo_plugin" / "preferences.toml"
        )
        assert not plugin.data_dir().exists()

    with pytest.raises(plugin.FilesystemClosed):
        plugin.data_dir()


def test_plugin_metadata_declares_the_settings():
    assert filesystem.__plugin_meta__.name == "filesystem"
    assert filesystem.__plugin_meta__.config is filesystem.Settings
