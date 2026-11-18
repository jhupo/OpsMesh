"""Exercise host-controlled plugin admission and revocation reporting."""

import sys
from types import ModuleType

import pytest

from opsmesh.plugins import (
    plugin_environment,
    register_plugin_manager,
    registry,
)
from opsmesh.plugins.errors import PluginLoadDenied, PluginReloadError
from opsmesh.plugins.manager import PluginManager


@pytest.fixture
def plugin_path(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(registry, "_plugins", {})
    monkeypatch.setattr(
        registry, "_module_plugins",
        {plugin.module_name: plugin for plugin in registry._plugins.values()},
    )
    yield tmp_path
    for name, module in list(sys.modules.items()):
        if name.startswith("admission_test_") and isinstance(
            module, ModuleType
        ):
            del sys.modules[name]


def new_manager(plugin_path, name, source="value = 1\n"):
    (plugin_path / f"{name}.py").write_text(source, encoding="utf-8")
    return PluginManager([name])


def test_admission_denial_stops_the_import(plugin_path):
    name = "admission_test_denied"
    manager = new_manager(
        plugin_path,
        name,
        "raise AssertionError('plugin source must not run')\n",
    )
    requests = []
    with plugin_environment(admission=lambda request: requests.append(request) or False):
        register_plugin_manager(manager)
        with pytest.raises(PluginLoadDenied, match="not enabled for this host"):
            manager.load_plugin(name)

    assert name not in sys.modules
    assert len(requests) == 1
    assert requests[0].plugin_id == name
    assert requests[0].module_name == name
    assert requests[0].origin is not None
    assert requests[0].origin.endswith(f"{name}.py")


def test_admission_returns_none_to_allow_the_load(plugin_path):
    name = "admission_test_allowed"
    manager = new_manager(plugin_path, name)
    requests = []

    with plugin_environment(admission=lambda request: requests.append(request) or None):
        register_plugin_manager(manager)
        assert manager.load_plugin(name) is not None
    assert [request.module_name for request in requests] == [name]


def test_admission_errors_are_not_reported_as_import_failures(plugin_path):
    name = "admission_test_unavailable"
    manager = new_manager(plugin_path, name)

    def unavailable(request):
        raise RuntimeError("admission backend unavailable")

    with plugin_environment(admission=unavailable):
        register_plugin_manager(manager)
        with pytest.raises(RuntimeError, match="admission backend unavailable"):
            manager.load_plugin(name)
    assert name not in sys.modules


def test_revoked_plugin_cannot_be_loaded_again(plugin_path):
    name = "admission_test_revoked"
    manager = new_manager(plugin_path, name)
    with plugin_environment():
        register_plugin_manager(manager)
        plugin = manager.load_plugin(name)
        assert plugin is not None
        registry.revoke_plugin(plugin)
        with pytest.raises(PluginReloadError, match="restart the host"):
            manager.load_plugin(name)


def test_environment_owns_finder_and_loaded_records(plugin_path):
    from opsmesh.plugins import get_available_plugin_names

    name = "admission_test_scope"
    manager = new_manager(plugin_path, name)
    original = tuple(sys.meta_path)
    with plugin_environment():
        register_plugin_manager(manager)
        assert tuple(sys.meta_path) != original
        assert get_available_plugin_names() == {name}
        assert registry.get_plugin(name) is None
        plugin = manager.load_plugin(name)
        assert registry.get_plugin(name) is plugin
    assert tuple(sys.meta_path) == original
    assert not get_available_plugin_names()
    assert registry.get_plugin(name) is None
    assert sys.modules[name] is plugin.module
