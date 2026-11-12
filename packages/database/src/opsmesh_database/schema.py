"""Own plugin schema declarations and their reversible registrations."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import MetaData

from opsmesh.framework import get_driver, on_plugin_loaded
from opsmesh.framework.plugins import require
from opsmesh.framework.plugins.model import Plugin
from opsmesh.framework.plugins.registry import track_registration

from .errors import MigrationError


@dataclass(frozen=True)
class DatabaseSchema:
    """A plugin's native metadata, migration directory, and required head."""

    metadata: MetaData
    directory: Path
    head: str


@dataclass(frozen=True)
class MigrationSource:
    """A stable plugin identity and its independent revision history."""

    owner: str
    directory: Path
    head: str

    def __post_init__(self) -> None:
        """Validate identity and resolve the trusted migration directory."""
        if (
            not isinstance(self.owner, str)
            or not self.owner
            or len(self.owner) > 200
            or any(
                character.isspace() or ord(character) < 32
                for character in self.owner
            )
        ):
            raise MigrationError(
                "A stable nonempty plugin identity is required"
            )
        if not isinstance(self.head, str) or not self.head.strip():
            raise MigrationError("A migration head revision is required")
        if any(character.isspace() for character in self.head):
            raise MigrationError("Migration heads cannot contain whitespace")
        directory = Path(self.directory).resolve()
        if not directory.is_dir():
            raise MigrationError(
                f"Migration directory does not exist: {directory}"
            )
        object.__setattr__(self, "directory", directory)

    @property
    def version_table(self) -> str:
        """Return a PostgreSQL-safe name without changing plugin identity."""
        digest = hashlib.sha256(self.owner.encode("utf-8")).hexdigest()[:40]
        return f"opsmesh_version_{digest}"


class SchemaRegistry:
    """Keep declaration ownership independent of Alembic execution."""

    def __init__(self) -> None:
        """Create an empty registry owned by the database plugin."""
        self._schemas: dict[str, DatabaseSchema] = {}
        self._sources: dict[str, MigrationSource] = {}

    def add(self, owner: str, schema: DatabaseSchema) -> Callable[[], None]:
        """Validate one declaration and return its exact rollback action."""
        source = MigrationSource(owner, schema.directory, schema.head)
        if owner in self._sources:
            raise MigrationError(
                f"A schema is already registered for {owner!r}"
            )
        if any(
            item.directory == source.directory
            for item in self._sources.values()
        ):
            raise MigrationError("A migration directory already has an owner")
        self._schemas[owner] = schema
        self._sources[owner] = source

        def remove() -> None:
            if self._sources.get(owner) is source:
                self._sources.pop(owner)
                self._schemas.pop(owner)

        return remove

    def get(self, owner: str) -> MigrationSource:
        """Return the registered history for one plugin."""
        try:
            return self._sources[owner]
        except KeyError:
            raise MigrationError(
                f"No schema is registered for {owner!r}"
            ) from None

    def sources(self) -> tuple[MigrationSource, ...]:
        """Return histories in dependency registration order."""
        return tuple(self._sources.values())


registry = SchemaRegistry()


@on_plugin_loaded
def bind_schema(plugin: Plugin) -> None:
    """Check a consumer's schema before its startup hooks run."""
    if plugin.metadata is None or "database" not in plugin.metadata.extra:
        return
    schema = plugin.metadata.extra["database"]
    if not isinstance(schema, DatabaseSchema):
        raise TypeError("Plugin database metadata must be DatabaseSchema")
    require("opsmesh_database")
    track_registration(registry.add(plugin.id_, schema))

    async def check() -> None:
        from .migrations import check_migrations

        await check_migrations(plugin.id_)

    get_driver().on_startup(check)
