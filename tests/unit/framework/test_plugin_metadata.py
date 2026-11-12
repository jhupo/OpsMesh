"""Exercise native plugin metadata and import failure cleanup."""

import importlib
import sys
from types import ModuleType

import pytest

from opsmesh.plugins import load as plugin_load
from opsmesh.plugins import manager as plugin_manager
from opsmesh.plugins import registry
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
    for name, module in list(sys.modules.items()):
        if name.startswith("metadata_test_") and isinstance(module, ModuleType):
            del sys.modules[name]


def load_source(path, name, source):
    (path / f"{name}.py").write_text(source, encoding="utf-8")
    importlib.invalidate_caches()
    manager = PluginManager([name])
    registry._managers.append(manager)
    return manager.load_plugin(name)


def test_loader_preserves_native_metadata_without_calling_values(plugin_path):
    plugin = load_source(
        plugin_path,
        "metadata_test_native",
        """
from opsmesh.plugins import PluginMetadata
def deferred():
    raise AssertionError("Must not execute while loading")
__plugin_meta__ = PluginMetadata(
    name="Example", description="Example", usage="Example",
    extra={"entry": deferred},
)
""",
    )
    assert plugin is not None
    assert plugin.metadata.extra["entry"] is plugin.module.deferred


def test_plugin_without_metadata_still_loads(plugin_path):
    plugin = load_source(plugin_path, "metadata_test_plain", "value = 1\n")
    assert plugin is not None
    assert plugin.metadata is None


def test_plugin_cannot_overwrite_injected_plugin(plugin_path):
    source = """
from opsmesh.plugins import PluginMetadata
__plugin__ = PluginMetadata(name="x", description="x", usage="x")
"""
    assert load_source(plugin_path, "metadata_test_injected", source) is None
    assert registry.get_plugin("metadata_test_injected") is None


def test_same_metadata_key_in_different_plugins_is_independent(plugin_path):
    source = """
from opsmesh.plugins import PluginMetadata
__plugin_meta__ = PluginMetadata(
    name="Example", description="Example", usage="Example",
    extra={"entry": object()},
)
"""
    first = load_source(plugin_path, "metadata_test_first", source)
    second = load_source(plugin_path, "metadata_test_second", source)
    assert first.metadata.extra["entry"] is not second.metadata.extra["entry"]


@pytest.mark.parametrize(
    "failure",
    ['__plugin_meta__ = "invalid"', 'raise RuntimeError("Import failed")'],
)
def test_failed_loading_removes_plugin_and_matchers(plugin_path, failure):
    from opsmesh.matchers.registry import matchers

    previous = {item for group in matchers.values() for item in group}
    source = (
        "from opsmesh.plugins import on_message\n"
        "handler = on_message(priority=374)\n" + failure + "\n"
    )
    assert load_source(plugin_path, "metadata_test_rollback", source) is None
    assert registry.get_plugin("metadata_test_rollback") is None
    assert {item for group in matchers.values() for item in group} == previous


@pytest.mark.anyio
async def test_failed_plugin_removes_hooks_and_lifecycle_without_touching_host(
    plugin_path,
):
    from opsmesh.adapters import Channel
    from opsmesh.dispatch import hooks
    from opsmesh.drivers import Driver
    from opsmesh.lifecycle import Lifespan

    module = ModuleType("metadata_test_resources")
    module.lifespan = Lifespan()
    module.events = []
    sys.modules[module.__name__] = module
    module.lifespan.on_startup(lambda: module.events.append("host"))
    processors = (
        hooks._event_preprocessors,
        hooks._event_postprocessors,
        hooks._run_preprocessors,
        hooks._run_postprocessors,
        Driver._connection_hook,
        Driver._disconnection_hook,
        Channel._calling_hook,
        Channel._called_hook,
    )
    before = [set(group) for group in processors]
    source = """
from metadata_test_resources import lifespan, events
from opsmesh.dispatch import (
    event_preprocessor, event_postprocessor, run_preprocessor, run_postprocessor
)
from opsmesh.plugins import on_message
from opsmesh.drivers import Driver
from opsmesh.adapters import Channel
@Driver.on_connect
@Driver.on_disconnect
async def connection(channel):
    pass
@Channel.on_calling
@Channel.on_called
async def operation(*args):
    pass
@event_preprocessor
@event_postprocessor
@run_preprocessor
@run_postprocessor
async def observer():
    pass
@lifespan.on_startup
@lifespan.on_ready
@lifespan.on_shutdown
def callback():
    events.append("failed plugin")
on_message(priority=381)
raise ValueError("broken import")
"""
    assert (
        load_source(plugin_path, "metadata_test_failed_hooks", source) is None
    )
    assert [set(group) for group in processors] == before
    async with module.lifespan:
        pass
    assert module.events == ["host"]


@pytest.mark.anyio
async def test_revoke_preserves_other_owners_and_cleans_in_reverse_order(
    plugin_path,
):
    from opsmesh.dispatch import hooks
    from opsmesh.lifecycle import Lifespan
    from opsmesh.plugins import revoke_plugin

    module = ModuleType("metadata_test_shared")
    module.lifespan = Lifespan()
    module.events = []
    exec(
        "async def observer():\n    pass\n"
        "def callback():\n    events.append('callback')\n",
        module.__dict__,
    )
    sys.modules[module.__name__] = module
    source = """
from metadata_test_shared import observer, callback, lifespan, events
from opsmesh.dispatch import event_preprocessor
from opsmesh.plugins.registry import track_registration
event_preprocessor(observer)
lifespan.on_startup(callback)
track_registration(lambda: events.append("first"))
track_registration(lambda: events.append("second"))
"""
    before = set(hooks._event_preprocessors)
    first = load_source(plugin_path, "metadata_test_owner_a", source)
    second = load_source(plugin_path, "metadata_test_owner_b", source)
    try:
        revoke_plugin(first)
        revoke_plugin(first)
        assert module.events == ["second", "first"]
        assert len(hooks._event_preprocessors - before) == 1
        async with module.lifespan:
            pass
        assert module.events[-1] == "callback"
    finally:
        revoke_plugin(second)
    assert set(hooks._event_preprocessors) == before


def test_installation_config_is_owned_and_available_after_import(plugin_path):
    from opsmesh.plugins import load_plugin, revoke_plugin

    name = "metadata_test_configured"
    (plugin_path / f"{name}.py").write_text(
        """
from pydantic import BaseModel
from opsmesh.plugins import get_plugin_config
class Settings(BaseModel):
    choices: list[str]
def settings():
    return get_plugin_config(Settings, namespace="installed")
initial = settings()
""",
        encoding="utf-8",
    )
    source = {"choices": ["saved"]}
    plugin = load_plugin(name, config=source, namespace="installed")
    try:
        source["choices"].append("changed")
        plugin.module.initial.choices.append("local")
        assert plugin.module.settings().choices == ["saved"]
    finally:
        revoke_plugin(plugin)
