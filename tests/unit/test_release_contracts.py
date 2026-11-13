"""Check source-independent loading and plugin command ownership."""

import json
import os
import platform
import py_compile
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from opsmesh.plugins.database import migrations, schema
from opsmesh.plugins.storage import PluginRecord, PluginStorage

ROOT = Path(__file__).resolve().parents[2]


def test_conversion_history_belongs_to_maintenance_tool():
    directory = ROOT / "tools/database_history/migrations/execution"
    source = schema.MigrationSource(
        "execution", directory, "0001_execution_observations"
    )

    assert migrations.revision_graph(source).get_heads() == [source.head]
    assert list(directory.glob("*.py"))
    assert directory.parent.parent == ROOT / "tools/database_history"


def test_runtime_bundle_removes_only_host_owned_files(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    build = runpy.run_path(str(ROOT / "scripts/build_plugins.py"))
    site = tmp_path / "site"
    metadata = site / "opsmesh-0.1.0.dist-info"
    metadata.mkdir(parents=True)
    package = site / "opsmesh"
    package.mkdir()
    (package / "__init__.py").write_text("")
    namespace = site / "shared"
    namespace.mkdir()
    (namespace / "host.py").write_text("")
    (namespace / "plugin.py").write_text("preserve")
    (metadata / "METADATA").write_text("Name: opsmesh\nVersion: 0.1.0\n")
    (metadata / "RECORD").write_text(
        "opsmesh/__init__.py,,\nshared/host.py,,\n"
        "opsmesh-0.1.0.dist-info/METADATA,,\n"
        "opsmesh-0.1.0.dist-info/RECORD,,\n"
    )
    build["remove_host_dependencies"](
        site, [{"name": "opsmesh", "version": "0.1.0"}]
    )
    assert not package.exists()
    assert not metadata.exists()
    assert (namespace / "plugin.py").read_text() == "preserve"
    assert not (namespace / "host.py").exists()


def test_runtime_bundle_rejects_host_dependency_version_conflict(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    build = runpy.run_path(str(ROOT / "scripts/build_plugins.py"))
    metadata = tmp_path / "pydantic-9.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: pydantic\nVersion: 9.0.0\n")
    with pytest.raises(ValueError, match="conflicts with host"):
        build["remove_host_dependencies"](
            tmp_path, [{"name": "pydantic", "version": "2.14.3"}]
        )


@pytest.mark.parametrize(
    ("system", "machine", "python", "libc"),
    [
        ("win32", "x86_64", "3.12.3", ("glibc", "2.35")),
        ("linux", "aarch64", "3.12.3", ("glibc", "2.35")),
        ("linux", "x86_64", "3.13.0", ("glibc", "2.35")),
        ("linux", "x86_64", "3.12.3", ("musl", "2.35")),
        ("linux", "x86_64", "3.12.3", ("glibc", "2.39")),
    ],
)
def test_runtime_bundle_rejects_host_abi_mismatch(
    monkeypatch, system, machine, python, libc
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    build = runpy.run_path(str(ROOT / "scripts/build_plugins.py"))
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(platform, "python_version", lambda: python)
    monkeypatch.setattr(platform, "libc_ver", lambda: libc)
    with pytest.raises(ValueError, match="must match the host"):
        build["validate_host_runtime"](
            {"python": "3.12.3", "architecture": "x86_64", "glibc": "2.35"}
        )


def test_smoke_declares_independent_bytecode_migrations(tmp_path, monkeypatch):
    smoke = runpy.run_path(str(ROOT / "scripts/smoke_release.py"))
    plugins = smoke["database_plugins"](
        tmp_path, "postgresql+asyncpg://localhost/isolated"
    )
    monkeypatch.setattr(schema, "registry", schema.SchemaRegistry())
    for plugin in plugins[1:]:
        name = plugin["module"]
        directory = tmp_path / name / "migrations"
        schema.registry.add(
            name, schema.DatabaseSchema(directory, "initial")
        )

    migrations.validate_migrations()
    sources = schema.registry.sources()
    assert len({source.version_table for source in sources}) == 2
    assert all(not list(source.directory.glob("*.py")) for source in sources)


@pytest.mark.parametrize("with_database", [False, True])
def test_smoke_manifest_keeps_execution_without_database(
    tmp_path, monkeypatch, with_database
):
    smoke = runpy.run_path(str(ROOT / "scripts/smoke_release.py"))
    if with_database:
        monkeypatch.setenv(
            "OPSMESH_TEST_DSN", "postgresql+asyncpg://localhost/isolated"
        )
    else:
        monkeypatch.delenv("OPSMESH_TEST_DSN", raising=False)
    commands = []
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: commands.append(command)
    )
    plugin_path = tmp_path / "external-plugins/site"
    smoke["exercise"](
        tmp_path / "opsmesh",
        tmp_path,
        plugin_path=plugin_path,
        maintenance_only=True,
    )
    manifest = json.loads((tmp_path / "host.json").read_text())
    with PluginStorage(Path(manifest["data_dir"])) as storage:
        records = storage.list()
    modules = {record.module for record in records}

    assert "opsmesh.plugins.execution" in modules
    assert ("opsmesh.plugins.database" in modules) == with_database
    assert any("release_probe:verify" in command for command in commands)
    assert str(plugin_path) in {record.directory for record in records}
    assert "plugins" not in manifest


def test_release_execution_probe_needs_no_database(tmp_path):
    smoke = runpy.run_path(str(ROOT / "scripts/smoke_release.py"))
    package = tmp_path / "release_probe"
    package.mkdir()
    (package / "__init__.py").write_text(smoke["PLUGIN"])
    script = """
import asyncio
import sys
from opsmesh.drivers import base as driver_state
from opsmesh.config import Config, Env
from opsmesh.plugins import registry
from opsmesh.plugins.manager import PluginManager
from opsmesh.drivers import Driver

driver = Driver(Env(), Config())
driver_state.set_driver(driver)
for module, values in {
    "opsmesh.plugins.filesystem": {"root": sys.argv[1]},
    "opsmesh.plugins.channels": {"api": {"enabled": False, "identities": [
        {"id": "smoke", "token_ref": "OPSMESH_SMOKE_TOKEN"}
    ]}},
}.items():
    registry.register_plugin_manager(
        PluginManager([module], config=values)
    )
manager = PluginManager(["release_probe"])
registry.register_plugin_manager(manager)
plugin = manager.load_plugin("release_probe")
assert plugin is not None

async def main():
    async with driver.lifespan():
        assert await plugin.module.probe() == "ok"
        assert "opsmesh.plugins.database" not in sys.modules

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "data")],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(tmp_path)]),
            "OPSMESH_SMOKE_TOKEN": "test-only-token",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_migrations_accept_bytecode_without_source(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "registry", schema.SchemaRegistry())
    revision = tmp_path / "initial.py"
    revision.write_text("revision = 'initial'\ndown_revision = None\n")
    py_compile.compile(
        str(revision), cfile=str(revision.with_suffix(".pyc")), doraise=True
    )
    revision.unlink()
    schema.registry.add(
        "test", schema.DatabaseSchema(tmp_path, "initial")
    )
    migrations.validate_migrations()


@pytest.mark.parametrize("bytecode", [False, True])
def test_plugin_command_loads_resources_without_startup(tmp_path, bytecode):
    package = tmp_path / "release_test"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("""from importlib.resources import files
from opsmesh import on_startup

@on_startup
def startup():
    raise AssertionError("Maintenance must not start plugin resources")

async def check():
    assert files(__package__).joinpath("data.txt").read_text() == "resource"
""")
    (package / "data.txt").write_text("resource")
    if bytecode:
        py_compile.compile(
            str(source), cfile=str(source.with_suffix(".pyc")), doraise=True
        )
        source.unlink()
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    with PluginStorage(tmp_path / "data") as storage:
        storage.save(
            PluginRecord(
                module="release_test",
                directory=str(tmp_path),
                enabled=True,
                autoload=True,
                required=True,
            )
        )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "opsmesh.app",
            "--manifest",
            str(manifest),
            "--command",
            "release_test:check",
        ],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_command_cannot_import_an_undeclared_plugin(tmp_path):
    marker = tmp_path / "executed"
    (tmp_path / "forbidden.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({"data_dir": str(tmp_path / "data")}))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "opsmesh.app",
            "--manifest",
            str(manifest),
            "--command",
            "forbidden:check",
        ],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert not marker.exists()
