"""Exercise host manifest loading and controlled plugin startup."""

import json
import sys
from pathlib import Path
from types import ModuleType

import anyio
import pytest
from pydantic import ValidationError

from opsmesh import app as host
from opsmesh.framework import runtime
from opsmesh.framework.plugins import admission, get_plugin, registry
from opsmesh.framework.plugins import load as plugin_load
from opsmesh.framework.plugins import manager as plugin_manager


@pytest.fixture
def host_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_driver", None)
    monkeypatch.setattr(registry, "_plugins", {})
    managers = []
    monkeypatch.setattr(registry, "_managers", managers)
    monkeypatch.setattr(plugin_manager, "_managers", managers)
    monkeypatch.setattr(plugin_load, "_managers", managers)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    admission.clear_plugin_admission()
    for name, module in list(sys.modules.items()):
        if name.startswith("host_test_") and isinstance(module, ModuleType):
            del sys.modules[name]


def write_plugin(directory: Path, name: str, source: str) -> Path:
    (directory / f"{name}.py").write_text(source, encoding="utf-8")
    return directory / f"{name}.loaded"


def marker_source(name: str, extra: str = "") -> str:
    return (
        "from pathlib import Path\n"
        f"Path(__file__).with_name({name!r}).write_text('loaded')\n" + extra
    )


def manifest(*entries, **kwargs):
    return host.HostManifest(
        http=host.HTTPSettings(enabled=False),
        plugins=tuple(
            entry
            if isinstance(entry, host.PluginEntry)
            else host.PluginEntry(module=entry)
            for entry in entries
        ),
        **kwargs,
    )


async def wait_for(path: Path, timeout: float = 5.0) -> None:
    with anyio.fail_after(timeout):
        while not path.exists():
            await anyio.sleep(0.01)


async def run_until(host_manifest, directory, marker):
    stop = anyio.Event()
    started = anyio.Event()

    async def runner():
        started.set()
        await host.run_host(
            host_manifest, manifest_dir=directory, stop_event=stop
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(runner)
        await started.wait()
        await wait_for(marker)
        stop.set()


@pytest.mark.anyio
async def test_disabled_plugin_is_never_imported(host_dir):
    alpha = write_plugin(
        host_dir, "host_test_alpha", marker_source("host_test_alpha.loaded")
    )
    disabled = write_plugin(
        host_dir,
        "host_test_disabled",
        marker_source("host_test_disabled.loaded"),
    )
    await run_until(
        host.HostManifest(
            http=host.HTTPSettings(enabled=False),
            plugins=(
                host.PluginEntry(module="host_test_alpha"),
                host.PluginEntry(module="host_test_disabled", enabled=False),
            ),
        ),
        host_dir,
        alpha,
    )
    assert disabled.exists() is False
    assert "host_test_disabled" not in sys.modules
    assert get_plugin("host_test_alpha") is None


@pytest.mark.anyio
async def test_manifest_keeps_metadata_and_orders_dependencies(host_dir):
    write_plugin(
        host_dir,
        "host_test_meta_dep",
        """
from pathlib import Path
from opsmesh.framework import runtime
from opsmesh.framework.plugins import Plugin, PluginMetadata
assert isinstance(globals()["__plugin__"], Plugin)
__plugin_meta__ = PluginMetadata(
    name="Dependency", description="d", usage="u"
)
def start():
    with Path(__file__).with_name("order.log").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write("dependency\\n")
runtime.get_driver().on_startup(start)
""",
    )
    root = write_plugin(
        host_dir,
        "host_test_meta_root",
        """
from pathlib import Path
from opsmesh.framework import runtime
from opsmesh.framework.plugins import (
    Plugin, PluginMetadata, get_plugin, require,
)
assert isinstance(globals()["__plugin__"], Plugin)
__plugin_meta__ = PluginMetadata(name="Root", description="d", usage="u")
require("host_test_meta_dep")
assert get_plugin("host_test_meta_dep").metadata.name == "Dependency"
assert get_plugin("host_test_meta_root").dependencies == ["host_test_meta_dep"]
def start():
    path = Path(__file__).with_name("order.log")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("root\\n")
    Path(__file__).with_name("host_test_meta_root.loaded").write_text(
        "loaded", encoding="utf-8"
    )
runtime.get_driver().on_startup(start)
""",
    )
    await run_until(
        manifest(
            host.PluginEntry(module="host_test_meta_dep", autoload=False),
            "host_test_meta_root",
        ),
        host_dir,
        root,
    )
    assert (host_dir / "order.log").read_text(
        encoding="utf-8"
    ) == "dependency\nroot\n"


@pytest.mark.anyio
async def test_required_plugin_failure_stops_the_host(host_dir):
    alpha = write_plugin(
        host_dir, "host_test_alpha", marker_source("host_test_alpha.loaded")
    )
    write_plugin(
        host_dir,
        "host_test_broken",
        "raise RuntimeError('import failed')\n",
    )
    with pytest.raises(host.HostStartupError, match="host_test_broken"):
        await host.run_host(
            host.HostManifest(
                http=host.HTTPSettings(enabled=False),
                plugins=(
                    host.PluginEntry(module="host_test_alpha"),
                    host.PluginEntry(module="host_test_broken", required=True),
                ),
            ),
            manifest_dir=host_dir,
        )
    assert alpha.exists()
    assert get_plugin("host_test_alpha") is None
    assert admission._admission is None


@pytest.mark.anyio
async def test_disabled_dependency_cannot_be_loaded_by_require(host_dir):
    dependency = write_plugin(
        host_dir, "host_test_dep", marker_source("host_test_dep.loaded")
    )
    root = write_plugin(
        host_dir,
        "host_test_root",
        "from pathlib import Path\n"
        "from opsmesh.framework.plugins import require\n"
        "try:\n"
        "    require('host_test_dep')\n"
        "except Exception as error:\n"
        "    Path(__file__).with_name('host_test_root.loaded')"
        ".write_text(type(error).__name__)\n",
    )
    await run_until(
        host.HostManifest(
            http=host.HTTPSettings(enabled=False),
            plugins=(
                host.PluginEntry(
                    module="host_test_dep", enabled=False, autoload=False
                ),
                host.PluginEntry(module="host_test_root"),
            ),
        ),
        host_dir,
        root,
    )
    assert root.read_text(encoding="utf-8") == "PluginLoadDenied"
    assert dependency.exists() is False
    assert "host_test_dep" not in sys.modules


@pytest.mark.anyio
async def test_non_required_failure_is_isolated(host_dir):
    write_plugin(
        host_dir,
        "host_test_broken",
        "raise RuntimeError('import failed')\n",
    )
    ready = write_plugin(
        host_dir, "host_test_ready", marker_source("host_test_ready.loaded")
    )
    await run_until(
        manifest("host_test_broken", "host_test_ready"),
        host_dir,
        ready,
    )
    assert ready.exists()
    assert "host_test_broken" not in sys.modules
    assert get_plugin("host_test_ready") is None


@pytest.mark.anyio
async def test_expected_origin_mismatch_is_rejected(host_dir):
    alpha = write_plugin(
        host_dir, "host_test_alpha", marker_source("host_test_alpha.loaded")
    )
    (host_dir / "elsewhere.py").write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(host.HostStartupError, match="host_test_alpha"):
        await host.run_host(
            host.HostManifest(
                http=host.HTTPSettings(enabled=False),
                plugins=(
                    host.PluginEntry(
                        module="host_test_alpha",
                        required=True,
                        expected_origin="elsewhere.py",
                    ),
                ),
            ),
            manifest_dir=host_dir,
        )
    assert alpha.exists() is False


@pytest.mark.anyio
async def test_dependency_uses_its_declared_configuration(host_dir):
    root = write_plugin(
        host_dir,
        "host_test_a",
        "from pathlib import Path\n"
        "from opsmesh.framework.plugins import require\n"
        "require('host_test_b')\n"
        "Path(__file__).with_name('host_test_a.loaded').write_text('loaded')\n",
    )
    (host_dir / "host_test_b.py").write_text(
        "from pathlib import Path\n"
        "from pydantic import BaseModel\n"
        "from opsmesh.framework.plugins import get_plugin_config\n"
        "class Settings(BaseModel):\n"
        "    value: str\n"
        "config = get_plugin_config(Settings, namespace='host.test.b')\n"
        "Path(__file__).with_name('b.config').write_text(config.value)\n",
        encoding="utf-8",
    )
    await run_until(
        host.HostManifest(
            http=host.HTTPSettings(enabled=False),
            plugins=(
                host.PluginEntry(module="host_test_a"),
                host.PluginEntry(
                    module="host_test_b",
                    autoload=False,
                    config_namespace="host.test.b",
                    config={"value": "declared"},
                ),
            ),
        ),
        host_dir,
        root,
    )
    assert (host_dir / "b.config").read_text(encoding="utf-8") == "declared"


def test_manifest_rejects_config_without_namespace():
    with pytest.raises(ValidationError, match="config_namespace"):
        host.HostManifest(
            plugins=(
                host.PluginEntry(module="host_test_alpha", config={"a": 1}),
            )
        )


def test_manifest_rejects_duplicate_modules_and_conflicting_config():
    with pytest.raises(ValidationError, match="Duplicate plugin module"):
        host.HostManifest(
            plugins=(
                host.PluginEntry(module="host_test_alpha"),
                host.PluginEntry(module="host_test_alpha"),
            )
        )
    with pytest.raises(ValidationError, match="Conflicting config"):
        host.HostManifest(
            plugins=(
                host.PluginEntry(
                    module="host_test_a",
                    config_namespace="shared",
                    config={"value": 1},
                ),
                host.PluginEntry(
                    module="host_test_b",
                    config_namespace="shared",
                    config={"value": 2},
                ),
            )
        )


def test_load_manifest_resolves_declared_paths(host_dir):
    (host_dir / "plugins").mkdir()
    (host_dir / "entry.py").write_text("value = 1\n", encoding="utf-8")
    manifest_path = host_dir / "host.json"
    manifest_path.write_text(
        json.dumps(
            {
                "plugin_paths": ["plugins"],
                "plugins": [
                    {
                        "module": "host_test_alpha",
                        "expected_origin": "entry.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = host.load_manifest(manifest_path)
    assert loaded.plugin_paths == (str((host_dir / "plugins").resolve()),)
    assert loaded.plugins[0].expected_origin == str(
        (host_dir / "entry.py").resolve()
    )
