"""Exercise host-controlled plugin admission and revocation reporting."""

import sys
from types import ModuleType

import pytest

from opsmesh.plugins import (
    clear_plugin_admission,
    registry,
    set_plugin_admission,
)
from opsmesh.plugins import load as plugin_load
from opsmesh.plugins import manager as plugin_manager
from opsmesh.plugins.errors import PluginLoadDenied, PluginReloadError
from opsmesh.plugins.manager import PluginManager


@pytest.fixture
def plugin_path(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(registry, "_plugins", {})
    managers = []
    monkeypatch.setattr(registry, "_managers", managers)
    monkeypatch.setattr(plugin_manager, "_managers", managers)
    monkeypatch.setattr(plugin_load, "_managers", managers)
    yield tmp_path
    clear_plugin_admission()
    for name, module in list(sys.modules.items()):
        if name.startswith("admission_test_") and isinstance(
            module, ModuleType
        ):
            del sys.modules[name]


def new_manager(plugin_path, name, source="value = 1\n"):
    (plugin_path / f"{name}.py").write_text(source, encoding="utf-8")
    manager = PluginManager([name])
    registry._managers.append(manager)
    return manager


def test_admission_denial_stops_the_import(plugin_path):
    name = "admission_test_denied"
    manager = new_manager(
        plugin_path,
        name,
        "raise AssertionError('plugin source must not run')\n",
    )
    requests = []
    set_plugin_admission(lambda request: requests.append(request) or False)

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

    set_plugin_admission(lambda request: requests.append(request) or None)

    plugin = manager.load_plugin(name)
    assert plugin is not None
    assert [request.module_name for request in requests] == [name]


def test_admission_errors_are_not_reported_as_import_failures(plugin_path):
    name = "admission_test_unavailable"
    manager = new_manager(plugin_path, name)

    def unavailable(request):
        raise RuntimeError("admission backend unavailable")

    set_plugin_admission(unavailable)

    with pytest.raises(RuntimeError, match="admission backend unavailable"):
        manager.load_plugin(name)
    assert name not in sys.modules


def test_revoked_plugin_cannot_be_loaded_again(plugin_path):
    name = "admission_test_revoked"
    manager = new_manager(plugin_path, name)
    plugin = manager.load_plugin(name)
    assert plugin is not None

    registry.revoke_plugin(plugin)

    with pytest.raises(PluginReloadError, match="restart the host"):
        manager.load_plugin(name)
