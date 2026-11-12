"""Database failures callers may need to handle."""

from __future__ import annotations


class DatabaseError(Exception):
    """Base class for database plugin failures."""


class DatabaseUnavailableError(DatabaseError):
    """The database engine is not started or cannot be reached."""


class MigrationError(DatabaseError):
    """A registered migration set is invalid or failed to apply."""


class MigrationRequiredError(MigrationError):
    """The database schema is missing a required revision."""
