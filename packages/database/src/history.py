"""Explicit, one-time conversion of the former shared migration ledger."""

from __future__ import annotations

import json
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Integer, MetaData, Table, Text
from sqlalchemy.engine import Connection

from .connections import Database
from .errors import MigrationError
from .migrations import (
    CUTOVER_TABLE,
    current_revisions,
    lock_migrations,
    revision_graph,
)
from .schema import MigrationSource


class HistoricalSource(BaseModel):
    """One explicitly retained history, including disabled plugins."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    owner: str
    directory: str
    head: str


class HistoryManifest(BaseModel):
    """The complete trusted source inventory for a shared ledger cutover."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sources: list[HistoricalSource] = Field(min_length=1)


def read_manifest(path: Path) -> tuple[MigrationSource, ...]:
    """Resolve explicit source paths; never infer missing historical owners."""
    manifest = HistoryManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    sources = []
    for item in manifest.sources:
        if item.directory == "builtin:legacy-execution":
            if (
                item.owner != "execution"
                or item.head != "0001_execution_observations"
            ):
                raise MigrationError(
                    "The archived execution history has a fixed identity"
                )
            directory = (
                Path(__file__).with_name("_legacy_migrations") / "execution"
            )
        else:
            directory = Path(item.directory)
            if not directory.is_absolute():
                directory = path.resolve().parent / directory
        sources.append(MigrationSource(item.owner, directory, item.head))
    if len({source.owner for source in sources}) != len(sources):
        raise MigrationError("Historical source owners must be unique")
    if len({source.directory for source in sources}) != len(sources):
        raise MigrationError("Historical migration directories must be unique")
    return tuple(sources)


def convert_history(
    connection: Connection, sources: tuple[MigrationSource, ...]
) -> None:
    """Stamp existing history without upgrades or data deletion.

    The caller owns a transaction and, on PostgreSQL, the migration lock.
    Each supplied graph must be independent; cross-plugin revision edges
    require a separately reviewed migration plan instead of implicit rewriting.
    """
    graphs = {source.owner: revision_graph(source) for source in sources}
    owners: dict[str, str] = {}
    for owner, graph in graphs.items():
        for revision in graph.walk_revisions():
            if revision.revision in owners:
                raise MigrationError(
                    "Shared history contains ambiguous revision ownership"
                )
            owners[revision.revision] = owner
    legacy = current_revisions(connection, "alembic_version")
    if not legacy:
        raise MigrationError("No shared migration history exists to convert")
    unknown = legacy - owners.keys()
    if unknown:
        raise MigrationError(
            f"Missing historical migration sources: {sorted(unknown)!r}"
        )
    marker = Table(
        CUTOVER_TABLE,
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("legacy_heads", Text, nullable=False),
        Column("sources", Text, nullable=False),
    )
    if connection.dialect.has_table(connection, CUTOVER_TABLE):
        raise MigrationError("Shared history has already been converted")
    planned = []
    for source in sources:
        heads = frozenset(
            revision for revision in legacy if owners[revision] == source.owner
        )
        existing = current_revisions(connection, source.version_table)
        if existing and existing != heads:
            raise MigrationError(
                f"Conflicting independent history for {source.owner!r}"
            )
        if len(heads) > 1:
            raise MigrationError(f"Multiple applied heads for {source.owner!r}")
        planned.append((source, heads))
    for source, heads in planned:
        context = MigrationContext.configure(
            connection, opts={"version_table": source.version_table}
        )
        context.stamp(graphs[source.owner], tuple(heads) or "base")
    marker.create(connection)
    connection.execute(
        marker.insert().values(
            id=1,
            legacy_heads=json.dumps(sorted(legacy)),
            sources=json.dumps(
                [
                    {
                        "owner": source.owner,
                        "directory": str(source.directory),
                        "head": source.head,
                        "version_table": source.version_table,
                    }
                    for source in sources
                ]
            ),
        )
    )


async def convert_shared_history(database: Database, path: Path) -> None:
    """Convert an explicit inventory atomically under the migration lock."""
    sources = read_manifest(path)
    async with database.engine.begin() as connection:
        await lock_migrations(
            connection, database.settings.migration_lock_timeout_ms
        )
        await connection.run_sync(convert_history, sources)
