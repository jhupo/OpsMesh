"""Check wheel boundaries and source-independent database migrations."""

import py_compile
import runpy
import zipfile
from pathlib import Path

import pytest

from database import migrations, schema

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "dependency", ["APScheduler==3.11.3", "pgvector==0.5.1"]
)
def test_framework_wheel_rejects_new_plugin_dependencies(tmp_path, dependency):
    build = runpy.run_path(str(ROOT / "scripts/build_packages.py"))
    wheel = tmp_path / "opsmesh.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("opsmesh/__init__.py", "")
        archive.writestr(
            "opsmesh-0.5.1.dist-info/METADATA",
            f"Name: opsmesh\nVersion: 0.5.1\nRequires-Dist: {dependency}\n",
        )
    with pytest.raises(ValueError, match="depends on a plugin runtime"):
        build["verify_wheel"](wheel)


def test_conversion_history_belongs_to_maintenance_tool():
    directory = ROOT / "tools/database_history/migrations/execution"
    source = schema.MigrationSource(
        "execution", directory, "0001_execution_observations"
    )

    assert migrations.revision_graph(source).get_heads() == [source.head]
    assert list(directory.glob("*.py"))
    assert directory.parent.parent == ROOT / "tools/database_history"


def test_migrations_accept_bytecode_without_source(tmp_path, monkeypatch):
    registry = schema.SchemaRegistry()
    monkeypatch.setattr(schema, "discover_schemas", lambda: registry)
    revision = tmp_path / "initial.py"
    revision.write_text("revision = 'initial'\ndown_revision = None\n")
    py_compile.compile(
        str(revision), cfile=str(revision.with_suffix(".pyc")), doraise=True
    )
    revision.unlink()
    registry.add("test", schema.DatabaseSchema(tmp_path, "initial"))
    migrations.validate_migrations()
