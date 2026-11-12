"""Verify independent migrations and safe concurrent database access."""

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import ValidationError
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from opsmesh.dependencies import Dependent
from opsmesh.dependencies.params import DefaultParam, DependParam
from opsmesh.params import Depends
import opsmesh.plugins.database as database
from opsmesh.plugins.database import connections, migrations, schema
from opsmesh.plugins.database.history import convert_history, read_manifest


def source(directory: Path, owner: str, head: str = "initial"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "initial.py").write_text(
        f"revision = {head!r}\ndown_revision = None\n"
        "def upgrade(): pass\ndef downgrade(): pass\n",
        encoding="utf-8",
    )
    return schema.MigrationSource(owner, directory, head)


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(schema, "registry", schema.SchemaRegistry())
    monkeypatch.setattr(connections, "_database", None)


def test_settings_require_async_postgresql():
    with pytest.raises(ValidationError):
        database.Settings(dsn="sqlite+aiosqlite:///test.db")


def test_nested_owner_has_a_stable_safe_table(tmp_path):
    item = source(tmp_path, "parent:child")
    assert (
        item.version_table
        == schema.MigrationSource(
            "parent:child", tmp_path, "initial"
        ).version_table
    )
    assert ":" not in item.version_table
    assert len(item.version_table) <= 63


def test_schema_registration_is_atomic_and_reversible(tmp_path):
    item = source(tmp_path, "parent:child")
    declaration = database.DatabaseSchema(MetaData(), item.directory, item.head)
    release = schema.registry.add(item.owner, declaration)
    with pytest.raises(database.MigrationError):
        schema.registry.add(item.owner, declaration)
    assert schema.registry.get(item.owner) == item
    release()
    assert not schema.registry.sources()


def test_migrations_reject_cross_plugin_revision_edges(tmp_path):
    item = source(tmp_path, "child")
    (tmp_path / "initial.py").write_text(
        "revision = 'initial'\ndown_revision = 'another_plugin_revision'\n"
    )
    with pytest.raises(database.MigrationError):
        migrations.revision_graph(item)


def test_disabling_an_independent_plugin_does_not_block_another(tmp_path):
    first = source(tmp_path / "first", "first", "a1")
    second = source(tmp_path / "second", "second", "b1")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        migrations.upgrade_source(connection, first)
        migrations.upgrade_source(connection, second)
        migrations._check_source(connection, first)
    engine.dispose()


def test_shared_history_requires_explicit_conversion(tmp_path):
    item = source(tmp_path, "first", "a1")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('a1')"))
        with pytest.raises(database.MigrationRequiredError, match="explicit"):
            migrations.upgrade_source(connection, item)
    engine.dispose()


def test_conversion_preserves_old_data_and_disabled_plugin_history(tmp_path):
    first = source(tmp_path / "first", "first", "a1")
    archived = read_manifest(_history_manifest(tmp_path))[0]
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version VALUES ('a1'), "
                "('0001_execution_observations')"
            )
        )
        connection.execute(text("CREATE TABLE execution_calls (call_id TEXT)"))
        connection.execute(
            text("INSERT INTO execution_calls VALUES ('retained')")
        )
        convert_history(connection, (first, archived))
        assert (
            connection.scalar(text("SELECT call_id FROM execution_calls"))
            == "retained"
        )
        assert migrations.current_revisions(
            connection, archived.version_table
        ) == frozenset({archived.head})
        assert (
            len(migrations.current_revisions(connection, "alembic_version"))
            == 2
        )
        migrations._check_source(connection, first)
        new = source(tmp_path / "new", "new", "new1")
        migrations.upgrade_source(connection, new)
        migrations._check_source(connection, new)
        with pytest.raises(database.MigrationError, match="already"):
            convert_history(connection, (first, archived))
    engine.dispose()


def _history_manifest(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        '{"sources": [{"owner": "execution", '
        '"directory": "builtin:legacy-execution", '
        '"head": "0001_execution_observations"}]}'
    )
    return path


def test_conversion_rejects_unknown_history_without_stamping(tmp_path):
    item = source(tmp_path, "first", "a1")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('missing')")
        )
        with pytest.raises(database.MigrationError, match="Missing historical"):
            convert_history(connection, (item,))
        assert not inspect(connection).has_table(item.version_table)
    engine.dispose()


@pytest.mark.anyio
async def test_parallel_dependencies_open_distinct_sessions(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    owner = connections.Database(
        database.Settings(dsn="postgresql+asyncpg://localhost/test")
    )
    owner._engine = engine
    owner._sessionmaker = async_sessionmaker(engine)
    monkeypatch.setattr(connections, "_database", owner)
    opened = []

    async def query(
        access: Annotated[database.Database, database.DatabaseAccess()],
    ):
        async with access.session() as session:
            opened.append(session)
            await asyncio.sleep(0)
            return await session.scalar(text("SELECT 1"))

    async def first(value: Annotated[int, Depends(query, use_cache=False)]):
        return value

    async def second(value: Annotated[int, Depends(query, use_cache=False)]):
        return value

    async def handler(
        a: Annotated[int, Depends(first)],
        b: Annotated[int, Depends(second)],
    ):
        return a, b

    dependent = Dependent.parse(
        call=handler, allow_types=(DependParam, DefaultParam)
    )
    try:
        async with AsyncExitStack() as stack:
            assert await dependent(stack=stack, dependency_cache={}) == (1, 1)
        assert len(opened) == 2 and opened[0] is not opened[1]
    finally:
        await owner.shutdown()


@pytest.mark.anyio
async def test_explicit_transaction_commits_and_rolls_back():
    engine = create_async_engine("sqlite+aiosqlite://")
    owner = connections.Database(
        database.Settings(dsn="postgresql+asyncpg://localhost/test")
    )
    owner._engine = engine
    owner._sessionmaker = async_sessionmaker(engine)
    try:
        async with owner.transaction() as session:
            await session.execute(text("CREATE TABLE items (value INTEGER)"))
            await session.execute(text("INSERT INTO items VALUES (1)"))
        with pytest.raises(RuntimeError):
            async with owner.transaction() as session:
                await session.execute(text("INSERT INTO items VALUES (2)"))
                raise RuntimeError("abort")
        async with owner.session() as session:
            assert (
                await session.scalar(text("SELECT sum(value) FROM items")) == 1
            )
    finally:
        await owner.shutdown()


def test_conversion_preserves_applied_revision_before_new_upgrade(tmp_path):
    item = source(tmp_path, "first", "a1")
    (tmp_path / "next.py").write_text(
        "from alembic import op\n"
        "revision = 'a2'\ndown_revision = 'a1'\n"
        "def upgrade(): op.execute('CREATE TABLE new_items (id INTEGER)')\n"
        "def downgrade(): pass\n"
    )
    item = schema.MigrationSource("first", tmp_path, "a2")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('a1')"))
        convert_history(connection, (item,))
        assert migrations.current_revisions(
            connection, item.version_table
        ) == frozenset({"a1"})
        assert not inspect(connection).has_table("new_items")
        migrations.upgrade_source(connection, item)
        assert inspect(connection).has_table("new_items")
    engine.dispose()


def test_old_application_cannot_change_history_after_conversion(tmp_path):
    item = source(tmp_path, "first", "a1")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        )
        connection.execute(text("INSERT INTO alembic_version VALUES ('a1')"))
        convert_history(connection, (item,))
        connection.execute(
            text("UPDATE alembic_version SET version_num = 'unreviewed'")
        )
        with pytest.raises(database.MigrationRequiredError, match="changed"):
            migrations._check_source(connection, item)
    engine.dispose()


def test_conflicting_target_history_does_not_stamp_other_sources(tmp_path):
    first = source(tmp_path / "first", "first", "a1")
    second = source(tmp_path / "second", "second", "b1")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('a1'), ('b1')")
        )
        connection.execute(
            text(
                f"CREATE TABLE {second.version_table} "
                "(version_num TEXT PRIMARY KEY)"
            )
        )
        connection.execute(
            text(f"INSERT INTO {second.version_table} VALUES ('conflict')")
        )
        with pytest.raises(database.MigrationError, match="Conflicting"):
            convert_history(connection, (first, second))
        assert not inspect(connection).has_table(first.version_table)
    engine.dispose()
