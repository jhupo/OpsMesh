"""Verify dependency-ordered plugin lifecycle and failure isolation."""

import importlib
import sys
from types import ModuleType

import pytest

from opsmesh.framework.lifecycle import Lifespan
from opsmesh.framework.plugins import get_plugin, registry
from opsmesh.framework.plugins import load as plugin_load
from opsmesh.framework.plugins import manager as plugin_manager
from opsmesh.framework.plugins.manager import PluginManager
from opsmesh.framework.plugins.model import PluginStatus


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
        if name.startswith("lifecycle_test_") and isinstance(
            module, ModuleType
        ):
            del sys.modules[name]


def _host_module(name: str) -> ModuleType:
    module = ModuleType(name)
    module.lifespan = Lifespan()
    module.events = []
    sys.modules[name] = module
    return module


def _load(path, name, source, *, required=False):
    (path / f"{name}.py").write_text(source, encoding="utf-8")
    importlib.invalidate_caches()
    manager = PluginManager([name], required=required)
    registry._managers.append(manager)
    return manager.load_plugin(name)


@pytest.mark.anyio
async def test_require_records_dependency_and_orders_startup(plugin_path):
    host = _host_module("lifecycle_test_order_host")
    (plugin_path / "lifecycle_test_order_a.py").write_text(
        """
from lifecycle_test_order_host import lifespan, events
lifespan.on_startup(lambda: events.append("a"))
""",
        encoding="utf-8",
    )
    dependent = _load(
        plugin_path,
        "lifecycle_test_order_b",
        """
from lifecycle_test_order_host import lifespan, events
from opsmesh.framework.plugins import require
require("lifecycle_test_order_a")
lifespan.on_startup(lambda: events.append("b"))
""",
    )
    dependency = get_plugin("lifecycle_test_order_a")
    assert dependent is not None and dependency is not None
    assert dependent.dependencies == [dependency.id_]

    await host.lifespan.startup()
    try:
        assert host.events == ["a", "b"]
        assert dependency.ready and dependent.ready
    finally:
        await host.lifespan.shutdown()


@pytest.mark.anyio
async def test_non_required_failure_blocks_only_dependents(plugin_path):
    host = _host_module("lifecycle_test_isolation_host")
    dependency = _load(
        plugin_path,
        "lifecycle_test_dependency",
        """
from lifecycle_test_isolation_host import lifespan, events
def fail():
    events.append("dependency")
    raise RuntimeError("dependency failed")
lifespan.on_startup(fail)
""",
    )
    caller = _load(
        plugin_path,
        "lifecycle_test_caller",
        """
from lifecycle_test_isolation_host import lifespan, events
from opsmesh.framework.plugins import require
require("lifecycle_test_dependency")
lifespan.on_startup(lambda: events.append("caller"))
lifespan.on_ready(lambda: events.append("caller-ready"))
""",
    )
    independent = _load(
        plugin_path,
        "lifecycle_test_independent",
        """
from lifecycle_test_isolation_host import lifespan, events
lifespan.on_startup(lambda: events.append("independent"))
lifespan.on_ready(lambda: events.append("independent-ready"))
""",
    )
    assert caller is not None
    assert dependency is not None
    assert independent is not None
    assert caller.dependencies == [dependency.id_]

    await host.lifespan.startup()
    try:
        assert host.events == ["dependency", "independent", "independent-ready"]
        assert dependency.status is PluginStatus.FAILED
        assert caller.status is PluginStatus.BLOCKED
        assert caller.blocked_by == {dependency.id_}
        assert independent.ready
    finally:
        await host.lifespan.shutdown()
    assert dependency.status is PluginStatus.STOPPED


@pytest.mark.anyio
async def test_required_failure_aborts_and_runs_shutdown(plugin_path):
    host = _host_module("lifecycle_test_required_host")
    plugin = _load(
        plugin_path,
        "lifecycle_test_required",
        """
from lifecycle_test_required_host import lifespan, events
def fail():
    raise RuntimeError("required failed")
lifespan.on_startup(fail)
lifespan.on_shutdown(lambda: events.append("cleanup"))
""",
        required=True,
    )
    assert plugin is not None

    with pytest.raises(RuntimeError, match="required failed"):
        await host.lifespan.startup()
    assert host.events == ["cleanup"]
    assert not host.lifespan.started
    assert plugin.status is PluginStatus.STOPPED


@pytest.mark.anyio
async def test_decorated_callbacks_bind_to_managed_plugin(
    plugin_path, monkeypatch
):
    from types import SimpleNamespace

    from opsmesh.framework import runtime

    host = _host_module("lifecycle_test_decorators_host")
    driver = SimpleNamespace(
        on_startup=host.lifespan.on_startup,
        on_ready=host.lifespan.on_ready,
        on_stopping=host.lifespan.on_stopping,
        on_shutdown=host.lifespan.on_shutdown,
    )
    monkeypatch.setattr(runtime, "_driver", driver)
    plugin = _load(
        plugin_path,
        "lifecycle_test_decorated",
        """
from opsmesh.framework import on_startup, on_ready, on_stopping, on_shutdown
from lifecycle_test_decorators_host import events

@on_startup
async def startup():
    events.append("start")

@on_ready
async def ready():
    events.append("ready")

@on_stopping
async def stopping():
    events.append("drain")

@on_shutdown
async def shutdown():
    events.append("stop")
""",
    )
    assert plugin is not None
    assert host.events == []
    assert all(entry.plugin is plugin for entry in host.lifespan._startup_funcs)
    async with host.lifespan:
        assert plugin.ready
        assert host.events == ["start", "ready"]
    assert host.events == ["start", "ready", "drain", "stop"]


@pytest.mark.anyio
async def test_revocation_removes_decorated_callbacks(plugin_path, monkeypatch):
    from types import SimpleNamespace

    from opsmesh.framework import runtime

    host = _host_module("lifecycle_test_revoke_host")
    monkeypatch.setattr(
        runtime,
        "_driver",
        SimpleNamespace(
            on_startup=host.lifespan.on_startup,
            on_stopping=host.lifespan.on_stopping,
            on_shutdown=host.lifespan.on_shutdown,
        ),
    )
    plugin = _load(
        plugin_path,
        "lifecycle_test_revoked",
        """
from opsmesh.framework import on_startup, on_stopping
from lifecycle_test_revoke_host import events
@on_startup
async def startup():
    events.append("unexpected")
@on_stopping
async def stopping():
    events.append("unexpected drain")
""",
    )
    assert plugin is not None
    registry.revoke_plugin(plugin)
    async with host.lifespan:
        assert host.events == []


@pytest.mark.anyio
async def test_stopping_failure_still_releases_all_resources():
    lifespan = Lifespan()
    events = []

    async def drain():
        events.append("drain")
        raise ValueError("drain failed")

    lifespan.on_shutdown(lambda: events.append("dependency closed"))
    lifespan.on_shutdown(lambda: events.append("consumer closed"))
    lifespan.on_stopping(drain)
    lifespan.on_stopping(lambda: events.append("stop admission"))
    with pytest.raises(ExceptionGroup, match="Framework lifespan failed"):
        async with lifespan:
            pass
    assert events == [
        "stop admission",
        "drain",
        "consumer closed",
        "dependency closed",
    ]
    assert not lifespan.started
