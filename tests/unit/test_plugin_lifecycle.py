"""Plugin loading, require(), builtin plugins, and get_plugin_config contracts."""

import importlib
import json
import sys

from pydantic import BaseModel
import pytest

import opsmesh
from opsmesh import plugin as plugin_module
from opsmesh.config import Config, Env
from opsmesh.drivers.none import Driver as NoneDriver
from opsmesh.matcher import matchers
from opsmesh.plugin import inherit_supported_adapters

PREFIX = "lifecycle_probe_"


@pytest.fixture
def plugin_path(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    plugins_before = dict(plugin_module._plugins)
    managers_before = list(plugin_module._managers)
    yield tmp_path
    plugin_module._plugins.clear()
    plugin_module._plugins.update(plugins_before)
    plugin_module._managers[:] = managers_before
    for name in list(sys.modules):
        if (
            name.startswith(PREFIX)
            or name.startswith("opsmesh.plugins.")
            or name in {"auto_plugins", "dir_plugins", "nested_root"}
            or name.startswith(("auto_plugins.", "dir_plugins.", "nested_root."))
        ):
            del sys.modules[name]
    importlib.invalidate_caches()


def write_module(plugin_path, name, source):
    (plugin_path / f"{PREFIX}{name}.py").write_text(source, encoding="utf8")
    return f"{PREFIX}{name}"


def test_load_plugin_and_require_loads_provider(plugin_path):
    provider_id = write_module(plugin_path, "provider", "VALUE = 42\n")
    consumer_id = write_module(
        plugin_path,
        "consumer",
        f"from opsmesh import on_notice, require\n"
        f"PROVIDER = require({provider_id!r})\n"
        f"m = on_notice()\n",
    )

    consumer = plugin_module.load_plugin(consumer_id)
    provider = plugin_module.get_plugin(provider_id)
    assert consumer is not None
    assert provider is not None
    try:
        assert consumer.module.PROVIDER is provider.module
        assert consumer.module.m in matchers[consumer.module.m.priority]
    finally:
        consumer.module.m.destroy()


def test_load_from_toml_explicit_path(plugin_path, monkeypatch):
    driver = NoneDriver(Env(), Config())
    monkeypatch.setattr(opsmesh, "_driver", driver)
    monkeypatch.chdir(plugin_path)

    ext_id = write_module(plugin_path, "toml_ext", "TAG = 'ext'\n")
    dir_pkg = plugin_path / "auto_plugins"
    dir_pkg.mkdir()
    (dir_pkg / "worker.py").write_text(
        "from opsmesh import on_notice\nm = on_notice()\n", encoding="utf8"
    )
    toml_file = plugin_path / "pyproject.toml"
    toml_file.write_text(
        f'[tool.opsmesh]\nplugins = [{ext_id!r}]\nplugin_dirs = ["auto_plugins"]\n',
        encoding="utf8",
    )

    loaded = opsmesh.load_from_toml(toml_file)
    try:
        assert {p.id_ for p in loaded} == {ext_id, "worker"}
        assert opsmesh.get_plugin("worker") is not None
    finally:
        for p in loaded:
            for m in list(p.matcher):
                m.destroy()


def test_load_builtin_plugin_single_session(plugin_path, monkeypatch):
    driver = NoneDriver(Env(), Config())
    monkeypatch.setattr(opsmesh, "_driver", driver)

    plugin = opsmesh.load_builtin_plugin("single_session")
    try:
        assert plugin is not None
        assert plugin.id_ == "single_session"
        assert plugin.metadata is not None
    finally:
        from opsmesh.message import _event_preprocessors
        from opsmesh.plugins import single_session

        _event_preprocessors.difference_update(
            {d for d in _event_preprocessors if d.call is single_session.preprocess}
        )


def test_get_plugin_config_from_global_and_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        'PLUGIN_ENDPOINT="https://ops.internal"\nPLUGIN_NESTED__RETRIES=5\n',
        encoding="utf8",
    )
    monkeypatch.setenv("PLUGIN_TOKEN", "sec-token")

    class NestedCfg(BaseModel):
        retries: int = 1

    class PluginCfg(BaseModel):
        plugin_endpoint: str
        plugin_token: str
        plugin_nested: NestedCfg

    driver = NoneDriver(Env(environment="test"), Config(_env_file=env_file))
    monkeypatch.setattr(opsmesh, "_driver", driver)

    cfg = opsmesh.get_plugin_config(PluginCfg)
    assert cfg.plugin_endpoint == "https://ops.internal"
    assert cfg.plugin_token == "sec-token"
    assert cfg.plugin_nested.retries == 5


def test_load_plugins_ignores_private_and_supports_json_nested_and_adapters(
    plugin_path, monkeypatch
):
    monkeypatch.chdir(plugin_path)

    # 1. Directory scan ignores _private.py
    d = plugin_path / "dir_plugins"
    d.mkdir()
    (d / "_private.py").write_text("PRIVATE = True\n", encoding="utf8")
    (d / "pub.py").write_text(
        "from opsmesh.plugin import PluginMetadata\n"
        "__plugin_meta__ = PluginMetadata(\n"
        '    name="pub", description="", usage="",\n'
        '    supported_adapters={"~webhook", "~console"},\n'
        ")\n",
        encoding="utf8",
    )

    # 2. load_from_json loads explicit plugin + plugin_dirs
    extra = write_module(
        plugin_path,
        "json_extra",
        "from opsmesh.plugin import PluginMetadata\n"
        "__plugin_meta__ = PluginMetadata(\n"
        '    name="extra", description="", usage="",\n'
        '    supported_adapters={"~webhook"},\n'
        ")\n",
    )
    cfg_json = plugin_path / "plugins.json"
    cfg_json.write_text(
        json.dumps({"plugins": [extra], "plugin_dirs": ["dir_plugins"]}),
        encoding="utf8",
    )
    loaded = opsmesh.load_from_json(cfg_json)
    assert {p.id_ for p in loaded} == {extra, "pub"}
    assert opsmesh.get_plugin("_private") is None
    assert inherit_supported_adapters("dir_plugins.pub", extra) == {
        "opsmesh.adapters.webhook"
    }

    # 3. Nested sub-plugin receives "parent:child" ID
    root_pkg = plugin_path / "nested_root"
    sub_dir = root_pkg / "sub"
    sub_dir.mkdir(parents=True)
    (sub_dir / "child.py").write_text("CHILD = 1\n", encoding="utf8")
    (root_pkg / "__init__.py").write_text(
        "from pathlib import Path\n"
        "import opsmesh\n"
        'opsmesh.load_plugins(str(Path(__file__).parent / "sub"))\n',
        encoding="utf8",
    )
    parent = opsmesh.load_plugin("nested_root")
    child = opsmesh.get_plugin("nested_root:child")
    assert parent is not None
    assert child is not None
    assert child in parent.sub_plugins
    assert child.parent_plugin is parent
